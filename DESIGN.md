# Design

## Evidence Atlas

WattDocket is a geographic workspace for residents and journalists, with public records and source evidence carried through to operator tools. Keep the map, results, public cards, address lookup, and operator bench recognizably part of the same product.

Day mode uses a pale blue-gray canvas, white surfaces, dark blue-green text, blue actions, and teal selected records. Night mode uses a deep blue-gray canvas, raised slate surfaces, pale text, and brighter blue/teal accents. Warning, unavailable, and blocked states retain their labels and consistent semantic colors in both appearances. Define colors through the shared tokens in `web/src/unified-theme.css`; standalone policy pages mirror those roles.

The header exposes Auto, Day, and Night on every app route. Auto follows the device appearance; explicit choices persist in local storage and apply before paint. Map tiles alone receive the night treatment, with controls, attribution, markers, and labels styled independently.

Use the established Inter and Space Grotesk typography. Mobile prose is generally 18px, supporting text 16px, and metadata at least 14px. Controls should be at least 44px high. At narrow widths the four main destinations remain visible; longer labels wrap instead of shrinking. Atlas map, results, and detail panels become separate views, with the top of each view revealed on selection. Operator sections and reduction options stack, and navigation wraps. Long charts retain readable labels and allow horizontal scrolling within the chart.

Locations are approximate, not facility boundaries. Keep source dates, coverage, estimates, uncertainty, and missing information visible. Never imply that missing data is zero or that a reference load measures facility consumption.
