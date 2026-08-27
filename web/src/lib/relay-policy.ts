const MISSION_ID = /^mission-[0-9a-f]{20}$/;
const MAX_BODY_BYTES = 2_048;

export type RelayAction =
  | "mission.create"
  | "mission.restore"
  | "mission.resume"
  | "mission.pilot-attest";

export interface RelayDecision {
  action: RelayAction;
  upstreamPath: string;
  upstreamBody: string;
}

export class RelayPolicyError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "RelayPolicyError";
  }
}

function reject(status: number, code: string, message: string): never {
  throw new RelayPolicyError(status, code, message);
}

function parseObject(body: string): Record<string, unknown> {
  if (new TextEncoder().encode(body).byteLength > MAX_BODY_BYTES) {
    reject(413, "BODY_TOO_LARGE", "Mission command body is too large");
  }
  let value: unknown;
  try {
    value = JSON.parse(body);
  } catch {
    reject(400, "INVALID_JSON", "Mission command body must be valid JSON");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    reject(400, "INVALID_BODY", "Mission command body must be an object");
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    reject(400, "UNEXPECTED_FIELDS", `Expected only: ${wanted.join(", ")}`);
  }
}

export function authorizeRelayRequest(input: {
  method: string;
  path: string[];
  search: string;
  body: string;
}): RelayDecision {
  if (input.search) {
    reject(400, "QUERY_NOT_ALLOWED", "Mission relay query parameters are not allowed");
  }

  const method = input.method.toUpperCase();
  if (
    method === "GET" &&
    input.path.length === 2 &&
    input.path[0] === "missions" &&
    MISSION_ID.test(input.path[1])
  ) {
    if (input.body) reject(400, "BODY_NOT_ALLOWED", "Mission restore accepts no body");
    return {
      action: "mission.restore",
      upstreamPath: `/v1/missions/${input.path[1]}`,
      upstreamBody: "",
    };
  }
  if (method !== "POST") {
    reject(405, "METHOD_NOT_ALLOWED", "Mission relay accepts GET restore or POST commands only");
  }

  const value = parseObject(input.body);
  if (input.path.length === 1 && input.path[0] === "missions") {
    exactKeys(value, ["cruise_alt_ft", "departure", "destination"]);
    if (typeof value.departure !== "string" || !/^[A-Za-z0-9]{3,8}$/.test(value.departure)) {
      reject(400, "INVALID_DEPARTURE", "departure must be a 3-8 character identifier");
    }
    if (typeof value.destination !== "string" || value.destination.length < 3 || value.destination.length > 120) {
      reject(400, "INVALID_DESTINATION", "destination must be 3-120 characters");
    }
    if (!Number.isSafeInteger(value.cruise_alt_ft) || Number(value.cruise_alt_ft) < 500 || Number(value.cruise_alt_ft) > 12_500) {
      reject(400, "INVALID_ALTITUDE", "cruise_alt_ft must be an integer from 500 to 12500");
    }
    return {
      action: "mission.create",
      upstreamPath: "/v1/missions",
      upstreamBody: JSON.stringify({
        departure: value.departure.toUpperCase(),
        destination: value.destination,
        cruise_alt_ft: value.cruise_alt_ft,
      }),
    };
  }

  if (
    input.path.length === 3 &&
    input.path[0] === "missions" &&
    MISSION_ID.test(input.path[1]) &&
    input.path[2] === "attest"
  ) {
    exactKeys(value, ["confirm_dispatch", "eta", "grace_min"]);
    if (value.confirm_dispatch !== true) {
      reject(400, "ATTESTATION_REQUIRED", "confirm_dispatch=true is required");
    }
    if (typeof value.eta !== "string" || !/^(?:[01][0-9]|2[0-3]):[0-5][0-9]Z$/.test(value.eta)) {
      reject(400, "INVALID_ETA", "eta must be a UTC time such as 16:00Z");
    }
    if (!Number.isSafeInteger(value.grace_min) || Number(value.grace_min) < 15 || Number(value.grace_min) > 240) {
      reject(400, "INVALID_GRACE", "grace_min must be an integer from 15 to 240");
    }
    return {
      action: "mission.pilot-attest",
      upstreamPath: `/v1/missions/${input.path[1]}/attest`,
      upstreamBody: JSON.stringify({
        confirm_dispatch: true,
        eta: value.eta,
        grace_min: value.grace_min,
      }),
    };
  }

  if (
    input.path.length === 3 &&
    input.path[0] === "missions" &&
    MISSION_ID.test(input.path[1]) &&
    input.path[2] === "resume"
  ) {
    exactKeys(value, ["confirm_resume"]);
    if (value.confirm_resume !== true) {
      reject(400, "RESUME_CONFIRMATION_REQUIRED", "confirm_resume=true is required");
    }
    return {
      action: "mission.resume",
      upstreamPath: `/v1/missions/${input.path[1]}/resume`,
      upstreamBody: JSON.stringify({ confirm_resume: true }),
    };
  }

  reject(404, "COMMAND_NOT_ALLOWED", "Mission command is not available");
}
