# NextRole Visual Style Guide

This guide governs NextRole documentation illustrations and diagrams, including the root README.
Use it alongside the relevant source code and architecture docs: it defines presentation, not the
system's topology. Updated October 2026 to reflect the current README image family.

## Principles

- Make one question easy to answer per image: what the product does, how a workflow proceeds,
  how components connect, or how a client integrates.
- Prefer clear labels, whitespace, and explicit relationships over decorative detail.
- Keep the family recognizable through the N-and-rocket mark, navy text, blue and teal accents,
  rounded cards, and simple icons. There is no required number of decorative elements.
- Ground technical claims in current code. Distinguish implemented behavior from proposals and
  label illustrations separately from actual screenshots.
- Use a light, opaque canvas for README exports so the image remains legible in either GitHub theme.
  This does not prescribe the application's UI theme.

## Brand and logo

The canonical documentation logo is [`images/next-role-logo-transparent.png`](images/next-role-logo-transparent.png).
Reuse this asset with its original aspect ratio, transparency, silhouette, and colors. Do not redraw,
recolor, stretch, crop, or replace it with a generic rocket. Use it once near the top-left; do not use
additional versions as agent or infrastructure icons.

For authored SVG/HTML or other editable diagrams, embed the canonical asset directly and preserve it
in the export. At a 1536px canvas width, aim for a 120–170px logo bounding box and at least 24px of
clear space. Use the same logo size and title alignment within a diagram series; a product hero may
use a separate title composition.

Image generators can use the canonical logo as a reference, but reference conditioning does **not**
guarantee identical pixels. When producing a new image that requires exact brand reproduction,
generate the artwork with an empty logo area, then place the original asset using a deterministic
composition step. Retain that composition source. Do not repeatedly regenerate the logo to try to
achieve an exact match.

The October 2026 README PNGs are generated illustrations with visually matched logo renditions,
not exact embedded copies. They remain usable as the current illustration set, but are not logo
masters or editable diagram sources. Preserve their content and style when refining them; adopt
exact asset composition when next rebuilding the set.

## Color tokens

These are target values for authored graphics and future exports, not measured pixel values from
existing generated artwork. Use one palette consistently within a series.

| Role | Color | Use |
| --- | --- | --- |
| Canvas | `#FAF9F6` | Warm ivory default |
| Alternate canvas | `#FFFFFF` | White when required by the destination; use across the whole set |
| Primary ink | `#101D45` | Titles and node names |
| Secondary ink | `#365574` | Supporting descriptions |
| Blue | `#2463AC` | Primary connections and card borders |
| Teal | `#168575` | Selected groups, branches, and labeled return paths |
| Blue surface | `#F0F6FD` | Light card fill |
| Teal surface | `#EFF9F6` | Light group fill |
| Neutral rule | `#CDD5DF` | Dividers and unobtrusive boundaries |

Keep body text dark. Do not rely on color alone to distinguish relationships or status. Avoid large
background gradients. The original logo gradient (`#1565D8` → `#1D9BF0` → `#19B8A5` → `#56D98A`)
belongs primarily to the logo; using it in headings, badges, or arrows is optional, not a requirement.

## Canvas and typography

Use **1536 × 1024 (3:2)** as the baseline for the current README series. A 16:9 overview or a 4:5
vertical workflow is also appropriate when the content benefits. Choose the reading order first,
then the aspect ratio; never stretch artwork to fit a preset.

All sizes below refer to the 1536px-wide baseline and scale proportionally with the export:

| Element | Target size | Weight |
| --- | --- | --- |
| Image title | 56–76px | Bold |
| Subtitle | 30–40px | Regular |
| Card title | 28–36px | Semibold or bold |
| Body and connection labels | 24–28px | Regular |
| Nonessential footer | 20–24px | Regular |

Use Inter for authored diagrams; Geist, IBM Plex Sans, or Manrope are acceptable alternatives. Use
one family throughout a set. Generated images approximate typography; do not claim an exact font
unless it was explicitly rendered with that font.

Allow 40–64px outer margins and 24–32px card padding. Shorten labels or split the diagram before
shrinking essential text. Oversized hero headlines are acceptable only if the content remains easy
to read and the heading does not dominate the entire image.

Review at both full resolution and **800px display width**. Essential labels should remain readable
without zoom. At a narrow mobile width, the overall flow should remain understandable; provide
meaningful alt text and equivalent nearby prose rather than expecting dense diagrams to remain
fully readable. Footers must not carry information necessary to interpret the diagram.

## Cards, icons, and stage markers

Use rounded rectangles with 16–24px corner radii, white or lightly tinted fills, and 2–3px borders.
Separate groups through spacing and boundaries. Avoid nested cards unless containment conveys a
real relationship, such as agents running inside a server.

Shadows are optional. If needed, use one subtle treatment across the set, such as
`0 6px 18px rgba(36, 99, 172, 0.06)`. Flat cards are the default; gradient header pills are optional.

Use simple outline icons with consistent stroke weight. Small solid symbols are acceptable if
visually consistent. Avoid emoji, 3D objects, photorealistic icons, and invented vendor logos. Icons
supplement labels; they should not be the only way to identify a component.

Number workflow stages consistently, either `01`–`06` or circular badges. Plain numbers are the
current default. Do not add numbered badges to architecture components unless there is an actual
sequence to communicate.

## Layout by purpose

### Product overview

Show inputs, the product's main capability, and the useful outputs. Secondary capabilities may sit
in a separate supporting row. Label the result as an illustration; do not invent UI controls or
sample metrics that could be mistaken for a live screenshot.

### Workflow

Use a clear left-to-right or top-to-bottom sequence. A landscape workflow may wrap to a second row
when connectors make the continuation unambiguous. Show parallel work with a visible split and
join; do not imply that the branches are sequential.

For the career workflow, generation stages 1–5 are followed by stage 6 for targeted updates. The
owning agent handles an update; a return arrow must not imply that every edit reruns all stages.
Label a simplified loop as an example or explain its scope in nearby prose.

### Platform architecture

Choose grouping based on the real deployment and responsibility boundaries. Include the relevant
agents, server processes, storage, and analytics components at the level the reader needs. The
career supervisor and its specialists are one part of the platform, not a template for every
architecture diagram.

Separate a logical agent view from deployment detail when combining them would overcrowd the
page. Preserve important bypasses and direct connections, such as checkpoint/store access outside
the metadata service. Do not imply that a logical grouping is a separately deployed service.

### Integrations

Place clients, entry points, and the server in a consistent reading direction. Use literal endpoint
paths and name the protocol. Put material access restrictions beside the relevant entry points or
in a clearly associated note. External services and optional tracing should be distinguishable.

## Connections and their meaning

| Relationship | Default appearance | Label requirement |
| --- | --- | --- |
| Request, delegation, or execution order | Solid blue arrow | Name the action or protocol when ambiguous |
| Parallel execution branch | Solid blue or teal arrow | Explicit split/join or a parallel group label |
| Data access or transfer | Solid arrow, or dashed arrow to distinguish it from control flow | Name the data or operation, such as `SELECT only` or `checkpoints + memory` |
| Context dependency | Dashed arrow | State the context being supplied |
| Iteration or targeted update | Curved return arrow, teal by default | Name the update scope; do not imply automatic cascading |
| Containment | Boundary around nodes | Name the containing process or logical group |

Color is an accent, not a semantic contract. Dashed lines do not universally mean “optional” or
“asynchronous.” If a diagram uses line patterns to distinguish several relationship types, add a
small legend or label each connection so the meaning is explicit.

Keep arrow direction consistent with the stated meaning. Route around labels and nodes. Connect
to the actual responsible component; grouping boundaries are acceptable endpoints only when the
relationship applies to the whole group. Avoid unexplained crossings and unattached arrowheads.

## Delivery and review

1. Read this guide and verify diagram content against relevant source files. Keep proposed behavior
   visibly separate from implemented behavior.
2. Reuse a reference image from the current set for visual continuity, plus the canonical logo
   asset. A reference image is not evidence of current architecture.
3. Prefer editable SVG/HTML or diagram source for technical diagrams that will change frequently.
   Generated raster artwork is suitable for illustrations, but its labels and geometry are not
   independently editable.
4. Export high-resolution PNG with an opaque light background. Preserve transparency only in
   standalone logo assets or when the destination explicitly requires it.
5. Inspect the final export: spelling, tool names, endpoints, arrow direction, parallel branches,
   boundaries, logo treatment, clipping, and readability at 800px width.
6. Add descriptive alt text and maintain a prose explanation of the important relationships.
   Clearly distinguish a screenshot, a generated illustration, and a proposed architecture.
7. Retain editable sources or generation briefs when creating a new set. Record meaningful
   limitations, such as an approximate generated logo, rather than claiming exact compliance.
8. Run the repository's applicable checks after all edits. Do not alter unrelated artwork solely
   to satisfy decorative preferences.

## Reusable generation brief

> Create a NextRole documentation illustration on a warm ivory (#FAF9F6) opaque canvas. Use navy
> text, restrained blue (#2463AC) and teal (#168575) accents, rounded lightly tinted cards, simple
> consistent icons, and generous whitespace. Match the supplied reference's visual family. Use a
> 1536 × 1024 landscape canvas unless the supplied layout needs another ratio. Make essential text
> readable at 800px display width. Use only the supplied component names, relationships, and
> captions. Label ambiguous arrows, show true parallel splits and joins, and keep proposed behavior
> explicit. Avoid decorative gradients, compulsory badges, and invented components. Reserve a
> clear top-left area for the canonical logo to be embedded unchanged during final composition.

Append the exact title, component list, connections, labels, source evidence, and any limitations
to this brief. When requesting revisions, specify the smallest necessary change and preserve the
rest of the diagram.
