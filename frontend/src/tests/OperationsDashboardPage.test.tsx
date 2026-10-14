import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OperationsDashboardPage from "../pages/OperationsDashboardPage";
import type { Identity } from "../types";
import { makeOpsReferral, stubFetch } from "./fixtures";

const riley: Identity = {
  username: "ops.riley",
  token: "tok-ops.riley",
  role: "ops",
  display_name: "Riley Ops",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

test("renders referral rows for operations", async () => {
  vi.stubGlobal(
    "fetch",
    stubFetch({
      "/api/ops/referrals/": {
        body: { count: 1, next: null, previous: null, results: [makeOpsReferral()] },
      },
    })
  );

  render(<OperationsDashboardPage identity={riley} />);

  expect(await screen.findByText("lead-00099")).toBeInTheDocument();
  expect(screen.getByText("Jordan Sample")).toBeInTheDocument();
  expect(screen.getByText("offered")).toBeInTheDocument();
  expect(screen.getByText(/Carol: pending/)).toBeInTheDocument();
});

test("expands the event timeline", async () => {
  vi.stubGlobal(
    "fetch",
    stubFetch({
      "/api/ops/referrals/21/events/": {
        body: makeOpsReferral().events,
      },
      "/api/ops/referrals/": {
        body: { count: 1, next: null, previous: null, results: [makeOpsReferral()] },
      },
    })
  );

  render(<OperationsDashboardPage identity={riley} />);
  await userEvent.click(await screen.findByRole("button", { name: /timeline/i }));

  expect(screen.getByText("referral_received")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /apply override/i })).toBeInTheDocument();
});

test("rejects an unsafe override ID instead of converting it to unassign", async () => {
  const fetchMock = stubFetch({
    "/api/ops/referrals/21/events/": { body: makeOpsReferral().events },
    "/api/ops/referrals/": {
      body: { count: 1, next: null, previous: null, results: [makeOpsReferral()] },
    },
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<OperationsDashboardPage identity={riley} />);
  await userEvent.click(await screen.findByRole("button", { name: /timeline/i }));
  await userEvent.type(
    screen.getByLabelText(/reassign to agent id/i),
    "999999999999999999999999999999999999999999"
  );
  await userEvent.type(screen.getByLabelText(/override note/i), "Reviewed by operations");
  await userEvent.click(screen.getByRole("button", { name: /apply override/i }));

  expect(await screen.findByText(/positive agent id/i)).toBeInTheDocument();
  expect(fetchMock.mock.calls.map((call) => String(call[0]))).not.toContain(
    "/api/ops/referrals/21/override/"
  );
});

test("shows access error for non-ops identities", async () => {
  vi.stubGlobal(
    "fetch",
    stubFetch({
      "/api/ops/referrals/": { status: 403, body: { detail: "forbidden" } },
    })
  );

  render(<OperationsDashboardPage identity={riley} />);

  expect(await screen.findByText(/operations access required/i)).toBeInTheDocument();
});
