# app.py
# Streamlit dashboard adapted from the GIS Accelerator starter.
# Data: your saved GeoParquet results from nyc-spatial-analysis.
# Run: python -m streamlit run app.py

from pathlib import Path
import math

import altair as alt
import folium
import geopandas as gpd
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(page_title="NYC Hydrant Density", page_icon="🚒", layout="wide")

APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "hydrant_density.parquet"

COLORS = ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"]
DISPLAY_COLS = ["ntaname", "boroname", "hydrant_count", "area_km2", "density_per_km2"]


@st.cache_data
def load_data(path, modified_ns):
    """Read once per file version; keep full precision for filtering and metrics."""
    gdf = gpd.read_parquet(path)

    missing = set(DISPLAY_COLS) - set(gdf.columns)
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(sorted(missing)))
    if gdf.empty:
        raise ValueError("The Parquet file has no neighborhoods.")
    if gdf.crs is None:
        raise ValueError("The GeoParquet file needs a coordinate reference system.")
    if gdf.geometry.isna().any() or gdf.geometry.is_empty.any():
        raise ValueError("Some neighborhoods have missing or empty geometries.")
    for column in ["hydrant_count", "area_km2", "density_per_km2"]:
        gdf[column] = pd.to_numeric(gdf[column], errors="raise")
        if gdf[column].isna().any() or not gdf[column].map(math.isfinite).all():
            raise ValueError(f"{column} contains missing or non-finite values.")
    if (gdf["area_km2"] <= 0).any() or (gdf["hydrant_count"] < 0).any() or (gdf["density_per_km2"] < 0).any():
        raise ValueError("Areas must be positive; hydrant counts and densities cannot be negative.")

    # Keep all areas, including parks and neighborhoods with zero hydrants.
    return gdf.to_crs(epsg=4326)


def density_color(density):
    """Use the same five density classes as the existing webmap."""
    for threshold, color in zip([100, 150, 200, 300], COLORS):
        if density < threshold:
            return color
    return COLORS[-1]


def build_map(filtered):
    bounds = filtered.total_bounds
    center = [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2]
    m = folium.Map(location=center, zoom_start=11, tiles="OpenStreetMap")

    # Separate formatted text from the numeric values used for map colors.
    map_data = filtered[DISPLAY_COLS + [filtered.geometry.name]].copy()
    map_data["area_display"] = map_data["area_km2"].map(lambda value: f"{value:.2f}")
    map_data["density_display"] = map_data["density_per_km2"].map(lambda value: f"{value:.1f}")
    fields = ["ntaname", "boroname", "hydrant_count", "area_display", "density_display"]
    aliases = ["Neighborhood:", "Borough:", "Hydrants:", "Area (km²):", "Hydrants per km²:"]

    folium.GeoJson(
        map_data.to_json(),
        style_function=lambda feature: {
            "fillColor": density_color(feature["properties"]["density_per_km2"]),
            "color": "#333",
            "weight": 0.5,
            "fillOpacity": 0.7,
        },
        highlight_function=lambda feature: {"weight": 2.5, "color": "#000"},
        tooltip=folium.GeoJsonTooltip(fields=fields, aliases=aliases, sticky=False),
        popup=folium.GeoJsonPopup(fields=fields, aliases=aliases),
        name="Neighborhood density",
    ).add_to(m)
    m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    labels = ["< 100", "100–150", "150–200", "200–300", "300+"]
    rows = "".join(
        f'<div><span style="display:inline-block;width:16px;height:10px;background:{color};'
        f'border:1px solid #aaa;margin-right:6px"></span>{label}</div>'
        for color, label in zip(COLORS, labels)
    )
    legend = (
        '<div style="position:absolute;bottom:30px;left:10px;z-index:9999;'
        'background:white;color:#222;padding:10px;border-radius:5px;'
        'font:12px sans-serif;line-height:1.7;box-shadow:0 1px 5px #999">'
        '<b>Hydrants per km²</b>' + rows + '</div>'
    )
    m.get_root().html.add_child(folium.Element(legend))
    return m


def main():
    st.title("NYC Fire Hydrant Density")
    st.markdown(
        "Explore NYC spatial analysis results. "
        "Use the sidebar to filter by borough and hydrant density."
    )
    if not DATA_FILE.is_file():
        st.error("Data file not found. Place hydrant_density.parquet beside app.py.")
        st.stop()
    try:
        data = load_data(str(DATA_FILE), DATA_FILE.stat().st_mtime_ns)
    except Exception as error:
        st.error(f"Could not load the neighborhood data: {error}")
        st.stop()

    # Sidebar filters use the original numeric values, without rounding.
    st.sidebar.header("Filters")
    boroughs = sorted(data["boroname"].dropna().unique().tolist())
    selected_boroughs = st.sidebar.multiselect("Borough", boroughs, default=boroughs)
    density_min = float(math.floor(data["density_per_km2"].min()))
    density_max = float(math.ceil(data["density_per_km2"].max()))
    if density_min == density_max:
        density_range = (density_min, density_max)
        st.sidebar.caption(f"All neighborhoods have density {density_min:.1f} / km².")
    else:
        density_range = st.sidebar.slider(
            "Hydrant density (per km²)",
            min_value=density_min, max_value=density_max,
            value=(density_min, density_max), step=1.0, format="%.1f",
        )
    filtered = data[
        data["boroname"].isin(selected_boroughs)
        & data["density_per_km2"].between(*density_range)
    ].copy()

    st.caption(f"Showing {len(filtered):,} of {len(data):,} neighborhood tabulation areas. Parks and zero-hydrant areas are included.")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Neighborhoods", f"{len(filtered):,}")
    col2.metric("Hydrants in selection", f"{int(filtered['hydrant_count'].sum()):,}")
    col3.metric("Mean neighborhood density", f"{filtered['density_per_km2'].mean():.1f} / km²" if len(filtered) else "—")
    col4.metric("Highest density", f"{filtered['density_per_km2'].max():.1f} / km²" if len(filtered) else "—")
    st.caption("Mean density is the simple average of the selected neighborhoods' densities.")

    st.subheader("Map")
    if filtered.empty:
        st.warning("No neighborhoods match the filters. Select a borough or widen the density range.")
    else:
        st_folium(build_map(filtered), use_container_width=True, height=500, returned_objects=[])
        st.caption("Hover for details, click for a popup. Colors use fixed density classes across all filters.")

        st.subheader("Highest-density neighborhoods")
        ranking = filtered[DISPLAY_COLS].sort_values("density_per_km2", ascending=False).head(15)
        chart = alt.Chart(ranking).mark_bar(color="#e31a1c").encode(
            x=alt.X("density_per_km2:Q", title="Hydrants per km²"),
            y=alt.Y("ntaname:N", sort="-x", title=None),
            tooltip=[
                alt.Tooltip("ntaname:N", title="Neighborhood"),
                alt.Tooltip("boroname:N", title="Borough"),
                alt.Tooltip("density_per_km2:Q", title="Hydrants per km²", format=".1f"),
            ],
        ).properties(height=400)
        st.altair_chart(chart, width="stretch")

    st.subheader("Data table")
    table = filtered[DISPLAY_COLS].sort_values("density_per_km2", ascending=False).reset_index(drop=True)
    st.dataframe(
        table, width="stretch", hide_index=True,
        column_config={
            "ntaname": "Neighborhood", "boroname": "Borough",
            "hydrant_count": st.column_config.NumberColumn("Hydrants", format="%d"),
            "area_km2": st.column_config.NumberColumn("Area (km²)", format="%.2f"),
            "density_per_km2": st.column_config.NumberColumn("Hydrants per km²", format="%.1f"),
        },
    )
    st.download_button(
        "Download filtered table (CSV)", data=table.to_csv(index=False).encode("utf-8"),
        file_name="hydrant_density_filtered.csv", mime="text/csv",
    )

    st.sidebar.divider()
    st.sidebar.markdown("**Analysis:** [nyc-spatial-analysis](https://github.com/nwillemsen99/nyc-spatial-analysis)")
    st.sidebar.markdown(
        "**Source data:** NYC Open Data — "
        "[2020 NTAs](https://data.cityofnewyork.us/City-Government/2020-Neighborhood-Tabulation-Areas-NTAs-/9nt8-h7nd) "
        "and [NYCDEP hydrants](https://data.cityofnewyork.us/Environment/NYCDEP-Citywide-Hydrants/6pui-xhxz)"
    )
    st.sidebar.caption("Density does not assess hydrant condition or emergency access. Built from the GIS Accelerator Streamlit starter.")


if __name__ == "__main__":
    main()
