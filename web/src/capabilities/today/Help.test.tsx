// Help: the form that emails what the expert types to support (ADR 0022); always shown.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { renderAt } from "../../test/render";

test("the form is always shown", async () => {
  signedIn();
  renderAt("/help");
  expect(await screen.findByRole("textbox", { name: "Tell us what's wrong" })).toBeInTheDocument();
});

test("it posts the topic, the words and the page, then says thanks", async () => {
  signedIn();
  let body: unknown;
  server.use(http.post("/v1/help-requests", async ({ request }) => { body = await request.json(); return json(null, 202); }));
  const { user } = renderAt("/help");
  expect(await screen.findByText("Please don't include patient names or details.")).toBeInTheDocument();
  await user.click(screen.getByText("My account"));
  const box = screen.getByRole("textbox", { name: "Tell us what's wrong" });
  await user.type(box, "I cannot sign in");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("Thanks. We'll reply by email.")).toBeInTheDocument();
  expect(body).toEqual({ topic: "account", message: "I cannot sign in", page: "/help" });
});

test("too many messages today says to call", async () => {
  signedIn();
  server.use(http.post("/v1/help-requests", () => apiError(429, "help_limit")));
  const { user } = renderAt("/help");
  await user.type(await screen.findByRole("textbox", { name: "Tell us what's wrong" }), "hello");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("You've sent a lot of messages today. Please call us instead.")).toBeInTheDocument();
});

test("any other failure is a plain message and keeps the words", async () => {
  signedIn();
  server.use(http.post("/v1/help-requests", () => apiError(503, "help_unavailable")));
  const { user } = renderAt("/help");
  const box = await screen.findByRole("textbox", { name: "Tell us what's wrong" });
  await user.type(box, "hello");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("We couldn't send that. Please try again.")).toBeInTheDocument();
  expect(box).toHaveValue("hello");
});
