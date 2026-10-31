---
name: python-fallback
description: Build a chart create_chart cannot express — dual y-axes, annotations, subplots or small multiples, trend-line overlays, error bars — with a Python script run through execute, then publish it with create_chart(figure_path=...). Use only after confirming no create_chart kind fits.
---

# Python fallback

`create_chart` draws every supported kind from SQL, with no approval needed. Reach for this only when the visual itself is outside it: dual y-axes, annotations, subplots or small multiples, a trend line over a scatter, error bars.

Export the data first:

```
run_sql("SELECT ...", save_as="rows.csv")     -> returns an absolute path
```

Then write a script and run it with `execute` (this pauses for approval):

```python
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

df = pd.read_csv("/tmp/nextrole-analytics/<thread id>/rows.csv")
fig = make_subplots(specs=[[{"secondary_y": True}]])
fig.add_trace(go.Bar(x=df["day"], y=df["runs"], name="runs"))
fig.add_trace(go.Scatter(x=df["day"], y=df["cost_usd"], name="cost"), secondary_y=True)
fig.write_json("/tmp/nextrole-analytics/<thread id>/runs-and-cost.plotly.json")
```

Then publish it, and it renders like any other chart:

```
create_chart(kind="bar", title="Runs and cost per day",
             figure_path="/tmp/nextrole-analytics/<thread id>/runs-and-cost.plotly.json")
```

Scripts have no database credentials by design — data reaches them only through `save_as`. pandas and plotly are installed; do not install anything.

Do not set colors or a template in a custom figure. The interface themes every chart for light and dark at render time, and hard-coded colors survive into the wrong theme.
