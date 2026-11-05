// The only way a load script learns where to send traffic. Plain JavaScript with no k6 imports, so a Python
// guard test can run it with Node (api/tests/guards/test_foundation_load_scripts.py).
//
// Rule: load is generated only against a staging environment, named by STAGING_URL. Production is refused by
// an allow-list, not a deny-list: the host must end in a hosting suffix we use for staging AND carry the word
// "staging" as a whole dash- or dot-separated token AND carry no "prod", "production" or "live" token.
// Local hosts are allowed so the scripts can be rehearsed against a laptop stack.
// The error never repeats the address it was given.

export const HOST_SUFFIXES = [".up.railway.app", ".avodeck.com"];
export const FORBIDDEN_TOKENS = ["prod", "production", "live"];

const ORIGIN = /^(https?):\/\/([a-z0-9.-]+)(:[0-9]{1,5})?\/?$/i;
const LOCAL_HOSTS = ["localhost", "127.0.0.1"];

export function checkStagingUrl(raw) {
  if (typeof raw !== "string" || raw.trim() === "") {
    throw new Error("STAGING_URL is not set. Load tests run only against a staging environment.");
  }
  const match = ORIGIN.exec(raw.trim());
  if (match === null) {
    throw new Error("STAGING_URL must be an origin only (scheme, host, optional port): no path, no credentials.");
  }
  const scheme = match[1].toLowerCase();
  const host = match[2].toLowerCase();
  const port = match[3] || "";
  const base = `${scheme}://${host}${port}`;
  if (LOCAL_HOSTS.includes(host)) return base;
  if (scheme !== "https") throw new Error("STAGING_URL must use https.");
  const suffix = HOST_SUFFIXES.find((s) => host.endsWith(s));
  if (suffix === undefined) throw new Error("STAGING_URL host is not on the staging allow-list.");
  const tokens = host
    .slice(0, -suffix.length)
    .split(/[.-]/)
    .filter((t) => t !== "");
  if (!tokens.includes("staging") || tokens.some((t) => FORBIDDEN_TOKENS.includes(t))) {
    throw new Error("STAGING_URL host is not on the staging allow-list.");
  }
  return base;
}

// `env` is k6's __ENV (or any object with STAGING_URL).
export function requireStaging(env) {
  return checkStagingUrl(env === undefined || env === null ? undefined : env.STAGING_URL);
}
