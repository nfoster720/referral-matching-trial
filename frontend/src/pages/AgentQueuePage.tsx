import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import OfferCard from "../components/OfferCard";
import type { Identity, Offer } from "../types";

interface Props {
  identity: Identity | null;
}

export default function AgentQueuePage({ identity }: Props) {
  const [offers, setOffers] = useState<Offer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .get<Offer[]>("/api/offers/mine/")
      .then((data) => {
        // Hide referrals this agent shouldn't work.
        setOffers(data.filter((o) => o.eligibility_snapshot?.eligible !== false));
      })
      .catch((err) => {
        setError(
          err?.status === 403
            ? "This account does not have an agent queue."
            : "Could not load your queue."
        );
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function claim(offer: Offer) {
    setBusy(true);
    setToast(null);

    try {
      await api.post(`/api/offers/${offer.id}/claim/`);
      setOffers((prev) => prev.filter((item) => item.id !== offer.id));
      setToast(`Referral ${offer.referral.external_id} claimed successfully.`);
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : "Could not claim this referral. Please refresh and try again.";
      setToast(message);
      load();
    } finally {
      setBusy(false);
    }
  }

  async function decline(offer: Offer) {
    setBusy(true);
    setToast(null);
    try {
      await api.post(`/api/offers/${offer.id}/decline/`);
      setOffers((prev) => prev.filter((o) => o.id !== offer.id));
      setToast(`Passed on ${offer.referral.external_id}.`);
    } catch (err) {
      setToast(
        err instanceof ApiError
          ? err.message
          : "Could not decline this referral. Please refresh and try again."
      );
      load();
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <p className="state-note">Loading your queue…</p>;
  }
  if (error) {
    return (
      <div className="state-note error-banner">
        {error} <button onClick={load}>Retry</button>
      </div>
    );
  }

  return (
    <section>
      <h2>Referal Queue</h2>
      <p className="subtitle">Offers for {identity?.display_name ?? "you"}</p>
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
      {offers.length === 0 ? (
        <p className="state-note">
          No open offers right now. New referrals appear here automatically.
        </p>
      ) : (
        <div className="offer-grid">
          {offers.map((offer) => (
            <OfferCard
              key={offer.id}
              offer={offer}
              onClaim={claim}
              onDecline={decline}
              busy={busy}
            />
          ))}
        </div>
      )}
    </section>
  );
}
