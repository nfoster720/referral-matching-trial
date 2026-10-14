export interface Identity {
  username: string;
  token: string;
  role: "agent" | "ops";
  display_name: string;
}

export interface EligibilitySnapshot {
  eligible: boolean;
  reasons: string[];
}

export interface Referral {
  id: number;
  external_id: string;
  customer_name: string;
  customer_email: string;
  customer_phone: string;
  state: string;
  postal_code: string;
  estimated_price: number;
  intent_level: "browsing" | "warm" | "hot";
  status: string;
  created_at: string;
}

export interface Offer {
  id: number;
  referral: Referral;
  score: number;
  score_explanation: Record<string, number>;
  eligibility_snapshot: EligibilitySnapshot;
  status: string;
  offered_at: string;
  expires_at: string;
  responded_at: string | null;
}

export interface OpsOfferSummary {
  id: number;
  agent: string;
  score: number;
  status: string;
  offered_at: string;
  expires_at: string;
}

export interface OpsEvent {
  id: number;
  event_type: string;
  actor: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface OpsNotification {
  agent: string;
  status: "pending" | "processing" | "sent" | "failed";
  attempts: number;
  last_error_code: string;
  next_attempt_at: string;
}

export interface OpsReferral {
  id: number;
  external_id: string;
  customer_name: string;
  state: string;
  postal_code: string;
  estimated_price: number;
  intent_level: string;
  status: string;
  assigned_agent: { id: number; name: string; email: string } | null;
  age_minutes: number;
  offers: OpsOfferSummary[];
  notifications: OpsNotification[];
  events?: OpsEvent[];
  created_at: string;
}

export interface OpsReferralPage {
  count: number;
  next: string | null;
  previous: string | null;
  results: OpsReferral[];
}
