import { Fragment, useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Identity, OpsEvent, OpsReferral, OpsReferralPage } from "../types";

interface Props {
  identity: Identity | null;
}

function formatAge(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  return `${Math.floor(minutes / (24 * 60))}d`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export default function OperationsDashboardPage({ identity }: Props) {
  const viewer = identity?.display_name ?? "Operations";
  const [referrals, setReferrals] = useState<OpsReferral[]>([]);
  const [count, setCount] = useState(0);
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [minAge, setMinAge] = useState("");
  const [maxAge, setMaxAge] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [events, setEvents] = useState<Record<number, OpsEvent[]>>({});
  const [timelineLoading, setTimelineLoading] = useState<number | null>(null);
  const [overrideAgent, setOverrideAgent] = useState("");
  const [overrideNote, setOverrideNote] = useState("");
  const [overrideBusy, setOverrideBusy] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(
    (requestedOffset = offset) => {
      setLoading(true);
      setError(null);
      const params = new URLSearchParams({ limit: "50", offset: String(requestedOffset) });
      if (statusFilter) params.set("status", statusFilter);
      if (stateFilter.trim()) params.set("state", stateFilter.trim().toUpperCase());
      if (minAge.trim()) params.set("min_age_minutes", minAge.trim());
      if (maxAge.trim()) params.set("max_age_minutes", maxAge.trim());
      api
        .get<OpsReferralPage>(`/api/ops/referrals/?${params.toString()}`)
        .then((page) => {
          setReferrals(page.results);
          setCount(page.count);
          setOffset(requestedOffset);
        })
        .catch((err) => {
          setError(
            err?.status === 403
              ? "Operations access required. Switch to an ops identity."
              : errorMessage(err, "Could not load referrals.")
          );
        })
        .finally(() => setLoading(false));
    },
    [maxAge, minAge, offset, stateFilter, statusFilter]
  );

  useEffect(() => {
    load(0);
  }, [load]);

  async function toggleTimeline(referral: OpsReferral) {
    if (expanded === referral.id) {
      setExpanded(null);
      return;
    }
    setExpanded(referral.id);
    if (events[referral.id]) return;
    setTimelineLoading(referral.id);
    try {
      const timeline = await api.get<OpsEvent[]>(
        `/api/ops/referrals/${referral.id}/events/`
      );
      setEvents((previous) => ({ ...previous, [referral.id]: timeline }));
    } catch (err) {
      setNotice(errorMessage(err, "Could not load this timeline."));
    } finally {
      setTimelineLoading(null);
    }
  }

  async function submitOverride(referral: OpsReferral) {
    const trimmedId = overrideAgent.trim();
    const parsedId = Number(trimmedId);
    if (
      trimmedId !== "" &&
      (!/^[1-9]\d*$/.test(trimmedId) || !Number.isSafeInteger(parsedId))
    ) {
      setNotice("Enter a positive agent ID, or leave the field blank to unassign.");
      return;
    }
    if (!overrideNote.trim()) {
      setNotice("Add a note explaining this Operations override.");
      return;
    }
    setOverrideBusy(referral.id);
    setNotice(null);
    try {
      await api.post(`/api/ops/referrals/${referral.id}/override/`, {
        agent_id: trimmedId === "" ? null : parsedId,
        note: overrideNote.trim(),
      });
      setNotice(`Referral ${referral.external_id} overridden.`);
      setOverrideAgent("");
      setOverrideNote("");
      load(offset);
    } catch (err) {
      setNotice(errorMessage(err, "Override failed."));
    } finally {
      setOverrideBusy(null);
    }
  }

  if (loading) {
    return <p className="state-note">Loading referrals… this can take a moment.</p>;
  }
  if (error) {
    return (
      <div className="state-note error-banner">
        {error} <button onClick={() => load(offset)}>Retry</button>
      </div>
    );
  }

  return (
    <section>
      <h2>Operations Dashboard</h2>
      <p className="subtitle">Monitoring as {viewer}</p>
      {notice && (
        <div className="toast" role="status">
          {notice}
        </div>
      )}
      <div className="ops-filters">
        <label>
          Status{" "}
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All statuses</option>
            <option value="new">New</option>
            <option value="offered">Offered</option>
            <option value="assigned">Assigned</option>
            <option value="closed">Closed</option>
          </select>
        </label>
        <label>
          State{" "}
          <input
            value={stateFilter}
            maxLength={2}
            onChange={(event) => setStateFilter(event.target.value)}
            placeholder="All"
          />
        </label>
        <label>
          Min age (minutes){" "}
          <input
            type="number"
            min="0"
            value={minAge}
            onChange={(event) => setMinAge(event.target.value)}
          />
        </label>
        <label>
          Max age (minutes){" "}
          <input
            type="number"
            min="0"
            value={maxAge}
            onChange={(event) => setMaxAge(event.target.value)}
          />
        </label>
      </div>
      {referrals.length === 0 ? (
        <p className="state-note">No referrals match these filters.</p>
      ) : (
        <>
          <table className="ops-table">
            <thead>
              <tr>
                <th>Referral</th>
                <th>Customer</th>
                <th>Market</th>
                <th>Status</th>
                <th>Assigned agent</th>
                <th>Age</th>
                <th>Offers</th>
                <th>Notifications</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {referrals.map((r) => (
                <Fragment key={r.id}>
                  <tr>
                    <td>{r.external_id}</td>
                    <td>{r.customer_name}</td>
                    <td>
                      {r.state} {r.postal_code}
                    </td>
                    <td>
                      <span className={`status status-${r.status}`}>{r.status}</span>
                    </td>
                    <td>{r.assigned_agent?.name ?? "—"}</td>
                    <td>{formatAge(r.age_minutes)}</td>
                    <td>
                      {r.offers.map((offer) => (
                        <span
                          key={offer.id}
                          className={`chip chip-${offer.status}`}
                          title={offer.agent}
                        >
                          {offer.agent.split(" ")[0]}: {offer.status}
                        </span>
                      ))}
                    </td>
                    <td>
                      {r.notifications
                        .filter((notification) => notification.last_error_code)
                        .map((notification, index) => (
                          <span
                            key={`${notification.agent}-${index}`}
                            className="chip chip-declined"
                            title={`Next retry: ${new Date(notification.next_attempt_at).toLocaleString()}`}
                          >
                            {notification.agent}: retry {notification.attempts} (
                            {notification.last_error_code})
                          </span>
                        ))}
                    </td>
                    <td>
                      <button className="link-btn" onClick={() => toggleTimeline(r)}>
                        {expanded === r.id ? "Hide" : "Timeline"}
                      </button>
                    </td>
                  </tr>
                  {expanded === r.id && (
                    <tr className="detail-row">
                      <td colSpan={9}>
                        <div className="timeline">
                          <h4>Event timeline</h4>
                          {timelineLoading === r.id ? (
                            <p>Loading timeline…</p>
                          ) : (
                            <ol>
                              {(events[r.id] ?? []).map((event) => (
                                <li key={event.id}>
                                  <code>{new Date(event.created_at).toLocaleString()}</code>{" "}
                                  <strong>{event.event_type}</strong>
                                  {event.actor && <> — {event.actor}</>}
                                </li>
                              ))}
                            </ol>
                          )}
                          <div className="override-box">
                            <label>
                              Reassign to agent id:{" "}
                              <input
                                value={overrideAgent}
                                onChange={(event) => setOverrideAgent(event.target.value)}
                                placeholder="blank to unassign"
                              />
                            </label>
                            <label>
                              Override note{" "}
                              <input
                                value={overrideNote}
                                maxLength={500}
                                onChange={(event) => setOverrideNote(event.target.value)}
                                placeholder="Reason for reassignment"
                              />
                            </label>
                            <button
                              disabled={overrideBusy === r.id}
                              onClick={() => submitOverride(r)}
                            >
                              {overrideBusy === r.id ? "Applying…" : "Apply override"}
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
          <div className="pagination-controls">
            <button disabled={offset === 0} onClick={() => load(Math.max(0, offset - 50))}>
              Previous
            </button>
            <span>
              {offset + 1}–{Math.min(offset + referrals.length, count)} of {count}
            </span>
            <button
              disabled={offset + referrals.length >= count}
              onClick={() => load(offset + 50)}
            >
              Next
            </button>
          </div>
        </>
      )}
    </section>
  );
}
