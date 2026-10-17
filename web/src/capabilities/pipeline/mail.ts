// Opens the expert's own email program with a `mailto:` link the API built (kept apart so tests can
// watch it instead of leaving the page).
export const mail = {
  open(href: string): void {
    window.location.assign(href);
  },
};
