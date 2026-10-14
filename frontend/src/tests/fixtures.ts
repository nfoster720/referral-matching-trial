import type { Offer, OpsReferral } from "../types";

export function makeOffer(overrides: Partial<Offer> = {}): Offer {
  const referralOverrides = overrides.referral ?? {};
  return {
    id: 1,
    score: 0.87,
    score_explanation: { geo_fit: 1, price_fit: 0.7, performance: 0.9, total: 0.87 },
    eligibility_snapshot: { eligible: true, reasons: [] },
    status: "pending",
    offered_at: new Date(Date.now() - 60_000).toISOString(),
    expires_at: new Date(Date.now() + 8 * 60_000).toISOString(),
    responded_at: null,
    ...overrides,
    referral: {
      id: 11,
      external_id: "lead-00042",
      customer_name: "Casey Example",
      customer_email: "casey.example@example.com",
      customer_phone: "555-0100",
      state: "CO",
      postal_code: "80202",
      estimated_price: 550_000,
      intent_level: "hot",
      status: "offered",
      created_at: new Date().toISOString(),
      ...referralOverrides,
    },
  };
}

export function makeOpsReferral(overrides: Partial<OpsReferral> = {}): OpsReferral {
  return {
    id: 21,
    external_id: "lead-00099",
    customer_name: "Jordan Sample",
    state: "CO",
    postal_code: "80211",
    estimated_price: 425_000,
    intent_level: "warm",
    status: "offered",
    assigned_agent: null,
    age_minutes: 42,
    offers: [
      {
        id: 5,
        agent: "Carol Fixture",
        score: 0.9,
        status: "pending",
        offered_at: new Date().toISOString(),
        expires_at: new Date().toISOString(),
      },
    ],
    notifications: [],
    events: [
      {
        id: 7,
        event_type: "referral_received",
        actor: "intake-webhook",
        payload: {},
        created_at: new Date().toISOString(),
      },
    ],
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

type RouteMap = Record<string, { status?: number; body: unknown }>;

export function stubFetch(routes: RouteMap) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const match = Object.entries(routes).find(([path]) => url.startsWith(path));
    if (!match) {
      return new Response(JSON.stringify({ detail: "not found" }), { status: 404 });
    }
    const { status = 200, body } = match[1];
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
}
