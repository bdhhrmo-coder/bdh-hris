"""Batch 3 Item 4: SVG charts show exactly the data they are given."""

from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from . import charts


class SvgChartTests(SimpleTestCase):
    def test_section_bars_show_rates_no_data_and_target(self):
        rows = [{"section": SimpleNamespace(name="Laboratory Section"), "rate": Decimal("5.7")},
                {"section": SimpleNamespace(name="Dietary Section"), "rate": None}]
        svg = str(charts.section_rate_chart(rows, "Absenteeism", target=Decimal("5.0")))
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn(">5.7%<", svg)
        self.assertIn(">no data<", svg)
        self.assertIn("Target 5.0%", svg)
        self.assertEqual(svg.count('class="bar bar-h"'), 2)
        self.assertIn("#a3241f", svg)  # above target = red

    def test_pie_percentages_and_empty_state(self):
        svg = str(charts.status_pie_chart({"pending": 6, "approved": 4, "not_approved": 0}))
        self.assertIn("Pending: 6 (60%)", svg)
        self.assertIn("Approved: 4 (40%)", svg)
        self.assertNotIn("Not approved", svg)  # zero slices are left out
        self.assertIn("pie-sweep", svg)
        empty = str(charts.status_pie_chart({"pending": 0, "approved": 0, "not_approved": 0}))
        self.assertIn("No applications in this period", empty)

    def test_bars_line_and_escaping(self):
        bars = str(charts.count_bar_chart([{"label": "<25", "count": 3}, {"label": "25-34", "count": 0}], "Age"))
        self.assertIn("&lt;25", bars)
        self.assertNotIn("<25<", bars)
        self.assertIn(">3<", bars)
        line = str(charts.monthly_trend_chart([{"label": "Sep 2026", "count": 9}, {"label": "Oct 2026", "count": 1}]))
        self.assertIn('class="chart-line-path"', line)
        self.assertIn("Sep 2026: 9", line)
