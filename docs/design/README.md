# Screens (copy of the design canvas)

Claude Code can't open the canvas link (it's private on claude.ai), so a copy of every board lives here. The canvas stays the master; re-export when it changes (ask Claude in the chat to "export the canvas to docs/design").

- `images/<board>.png`: a picture of each screen at 1440px wide. **Look at this first** to see the layout.
- `screens/<board>.dc.html`: the same screen as HTML with inline styles. Use it for exact wording, spacing, colours and sizes. It is a mock-up, not production code: rebuild it with the design-system components in React; don't copy the markup.
- `screens/Main.dc.html` / `images/Main.png`: the design system (colours, type, buttons, badges). `Logo6`: the logo.
- `screens/canvas.json`: board titles and order.

The board number, title and owning slice for each file are in `docs/spec/design-canvas.md`.

Words on the boards are the launch packs' labels (England and Wales, general practice): "solicitor", "GP", "joint statement", "trial bundle", "statement of truth". In code these come from the pack vocabulary (rule 11); never hard-code them. Names, dates and numbers on the boards (Mrs A, Dr Morgan, Northgate Medico-Legal, page 412) are examples. Fonts in the images are fallbacks; the real fonts are Atkinson Hyperlegible, IBM Plex Mono and Outfit (logo).
