---
name: charts
description: Draw and save charts, dashboards, and written reports from warehouse queries. Use whenever an answer involves a trend, a comparison, a distribution, or a headline number worth showing, when the user asks to chart, plot, graph, visualise, or build a dashboard, and when a visual needs custom Python because create_chart cannot express it.
---

# Charts

`create_chart` runs your SQL, draws the result, and saves it to the
conversation. Charts persist: reopening the thread later still shows them.

## Chart or not

Draw when the shape is the answer: a trend over time, a comparison across
categories, a distribution, a two-dimensional pattern.

Do not draw when a sentence is the answer. One number is a sentence, or a `kpi`
if it deserves emphasis. Three rows are a `table`. A chart of three bars is
noise.

## Choosing a kind

| Question shape | Kind | Mapping |
| --- | --- | --- |
| How did X move over time? | `line` | `x` = date, `y` = measure |
| Same, several measures or groups | `line` | plus `series`, or a list of `y` |
| Same, composition over time | `area` with `stacked=True` | `x`, `y`, `series` |
| How do categories compare? | `bar` | `x` = category, `y` = measure |
| Composition within each category | `bar` with `stacked=True` | plus `series` |
| Share of a whole, few slices | `pie` | `x` = labels, `y` = values |
| How is a value distributed? | `histogram` | `x` = the column to bin |
| Two dimensions against one value | `heatmap` | `x` = columns, `series` = rows, `y` = value |
| Exact figures matter | `table` | no mapping |
| One headline number | `kpi` | `y` = value, `x` = optional label |

Use `pie` only up to about six slices; beyond that a bar chart reads better.

## Getting it right

- Aggregate in SQL. The tool refuses more than a few thousand rows, and a chart
  of raw rows is unreadable anyway.
- Order the query the way the chart should read: by date for a trend, by value
  descending for a ranked bar chart.
- Give every chart a title that states the finding, not the mechanism: "LLM cost
  per day by model", not "Query results".
- Put the caveat in `description`. Cost charts should say the figures are list
  price estimates; anything over message dates should say the timestamps are
  capture times.
- Set `y_format`: `currency` for dollars, `percent` for rates (pass the fraction,
  not 0-100), `duration_s` for seconds.
- Reuse a `slug` to replace a chart you already made rather than accumulating
  near-duplicates.

## After you draw one

`create_chart` puts the chart in the conversation itself — the user is already
looking at it, with its title, caveat and row count. So in your reply, write the
finding the chart shows; do not re-embed it with `![...](/charts/...)` and do not
describe the chart as an artefact ("the chart has been generated and saved").
Embedding belongs in saved reports, below, where there is no card.

## Dashboards

A dashboard is several charts plus the narrative that ties them together:

1. Draw each chart with `create_chart`, giving each a stable `slug`.
2. Write `/reports/<thread id>/<slug>.md` with your findings, embedding each
   chart where it belongs:

```markdown
## Reliability

Failures held at 3% of runs this week, all on Tuesday's deploy.

![Run failures per day](/charts/<thread id>/failures-per-day.plotly.json)
```

3. Tell the user the report path.

Lead each section with what the chart shows. A dashboard of unexplained charts
is a worse answer than three sentences.

## When create_chart cannot draw it

For a visual outside the supported kinds — a box plot, a funnel, dual axes,
annotations — build the figure yourself and publish it:

```
run_sql("SELECT ...", save_as="rows.csv")     -> returns an absolute path
```

Then write a script and run it with `execute` (this pauses for approval):

```python
import pandas as pd, plotly.express as px

df = pd.read_csv("/tmp/nextrole-analytics/<thread id>/rows.csv")
fig = px.box(df, x="model", y="duration_s")
fig.write_json("/tmp/nextrole-analytics/<thread id>/duration-by-model.plotly.json")
```

Then publish it, and it renders like any other chart:

```
create_chart(kind="scatter", title="Run duration by model",
             figure_path="/tmp/nextrole-analytics/<thread id>/duration-by-model.plotly.json")
```

Scripts have no database credentials by design — data reaches them only through
`save_as`. pandas and plotly are installed; do not install anything.

Do not set colors or a template in a custom figure. The interface themes every
chart for light and dark at render time, and hard-coded colors survive into the
wrong theme.
