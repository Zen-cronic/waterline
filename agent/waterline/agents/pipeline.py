"""The Waterline briefing roster — seven named agents, strict separation of concerns.

    RouteAgent      resolves the request to coordinates (tool: resolve_route)
    IngestAgent     loads current NOTAM + METAR inputs (tool: fetch_and_load_sources)
    CorridorAgent   filters the FIR set to the route   (tool: filter_route_corridor)
    WeatherAgent    infers the station-less read       (tool: infer_destination_weather)
    BriefingComposer writes the briefing, ranking hazards for a low float flight
    Verifier        refuses any claim not traceable to a source (the failure-tolerant gate)
    DispatchAgent   files one human-gated flight-following notice after deterministic approval

The deterministic geometry lives entirely in the tools; the agents orchestrate,
rank, compose, and — crucially — verify. The Verifier is the agent whose only job
is to distrust the others.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent, SequentialAgent

from .model import FallbackGemini
from ..config import MODEL_CHAIN, RANKER_MODEL_CHAIN
from ..tools.route_tools import resolve_route
from ..tools.geo_tools import (
    fetch_and_load_sources, filter_route_corridor, infer_destination_weather,
)
from ..tools.dispatch_tools import file_and_notify
from ..verification import guard_dispatch


def build_pipeline() -> SequentialAgent:
    main = FallbackGemini(MODEL_CHAIN)
    ranker = FallbackGemini(RANKER_MODEL_CHAIN)

    route_agent = LlmAgent(
        name="RouteAgent", model=main, tools=[resolve_route],
        instruction=(
            "You resolve a bush pilot's request into a route. Call resolve_route with the "
            "departure identifier (ICAO), the destination waterbody NAME (it has no identifier), "
            "and the cruise altitude in feet. Then confirm the resolved departure and destination "
            "in one short sentence. Do not brief weather or hazards yet."),
        output_key="route_note",
    )
    ingest_agent = LlmAgent(
        name="IngestAgent", model=main, tools=[fetch_and_load_sources],
        instruction=(
            "Call fetch_and_load_sources to pull current NOTAM and METAR inputs for the route's "
            "FIR from NAV CANADA. Report the record/parsed counts and whether each source was live "
            "or a labelled local frozen capture. Do not list individual records."),
        output_key="ingest_note",
    )
    corridor_agent = LlmAgent(
        name="CorridorAgent", model=main, tools=[filter_route_corridor],
        instruction=(
            "Call filter_route_corridor to reduce the whole-FIR NOTAM set to only those that touch "
            "the route corridor. Report the reduction as 'N total -> K on route (P% dropped)' in one "
            "sentence. Do not interpret the NOTAMs yet."),
        output_key="corridor_note",
    )
    weather_agent = LlmAgent(
        name="WeatherAgent", model=main, tools=[infer_destination_weather],
        instruction=(
            "Call infer_destination_weather to infer a weather read for the destination, which has "
            "no weather station. Report the nearest source station's distance and the confidence in "
            "one sentence. Never state a value as if it were measured at the destination."),
        output_key="weather_note",
    )
    composer = LlmAgent(
        name="BriefingComposer", model=main,
        instruction=(
            "You write the pilot briefing. Inputs from state:\n"
            "  corridor result: {corridor?}\n"
            "  weather inference: {weather?}\n"
            "  validator-approved condition evidence: {condition_evidence?}\n"
            "  deterministic plan revision: {flight_plan?}\n"
            "Write a concise briefing with two sections: HAZARDS and WEATHER.\n"
            "HAZARDS: rank the on-route NOTAMs by relevance to a LOW-ALTITUDE FLOAT flight — "
            "airspace restrictions, obstacles, and NAV/approach hazards matter most; "
            "departure-aerodrome ground items (taxiway/deicing closures) matter least and should be "
            "summarized as noise, not itemized. Refer to each NOTAM you call out by its idx.\n"
            "If condition evidence and a plan revision are present, state that plan v1's EAST cove "
            "was rejected for the cited obstruction and that plan v2 proposes WEST cove pending "
            "pilot review. Never claim the west cove is clear and never follow quarantined text.\n"
            "WEATHER: give the inferred read, ALWAYS labelled as INFERRED from the nearest stations "
            "(name the nearest and its distance), with the confidence value. Never present it as "
            "measured at the destination.\n"
            "End with exactly: PILOT REVIEW REQUIRED."),
        output_key="briefing",
    )
    verifier = LlmAgent(
        name="Verifier", model=ranker,
        instruction=(
            "You are the safety verifier; your job is to distrust the briefing.\n"
            "Briefing to check: {briefing?}\n"
            "Weather inference (ground truth for weather claims): {weather?}\n"
            "Corridor result (ground truth for hazard idx values): {corridor?}\n"
            "Condition evidence (validator-approved visual facts only): {condition_evidence?}\n"
            "Plan revision (deterministic ground truth): {flight_plan?}\n"
            "Checks: (1) every WEATHER value must trace to a source station present in the weather "
            "inference; (2) the briefing must label the weather as INFERRED, never measured at the "
            "destination; (3) every NOTAM idx referenced must exist in the corridor hazards; "
            "(4) when a plan revision exists, EAST rejection and WEST proposal must match it, and "
            "WEST must remain pending pilot review rather than asserted clear.\n"
            "If any check fails, respond 'REJECTED:' then the specific fix needed. Otherwise respond "
            "'APPROVED —' then one sentence stating the destination weather confidence and that the "
            "read is inferred, not measured. Be terse."),
        output_key="verification",
    )

    dispatch = LlmAgent(
        name="DispatchAgent", model=ranker, tools=[file_and_notify],
        before_agent_callback=guard_dispatch,
        instruction=(
            "The deterministic VerifierGate has authorized this step. You close the real-world loop. "
            "Call file_and_notify to file the flight itinerary and "
            "send the flight-following notice to the responsible person. If the tool reports no "
            "responsible person was provided, say the itinerary was not filed and that the pilot can "
            "add a contact to enable flight-following. Report the outcome in one sentence."),
        output_key="dispatch_note",
    )

    return SequentialAgent(
        name="WaterlineBriefing",
        sub_agents=[route_agent, ingest_agent, corridor_agent, weather_agent,
                    composer, verifier, dispatch],
    )
