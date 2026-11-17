"""Refresh the SVG calendar from the contribution days on a GitHub profile."""

import argparse
from calendar import month_abbr
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen


ASSET = Path(__file__).resolve().parents[1] / 'assets' / 'animation.svg'
START_MARKER = '<!-- BEGIN CONTRIBUTIONS -->'
END_MARKER = '<!-- END CONTRIBUTIONS -->'
COLORS = ('#17241e', '#19452e', '#1b6840', '#2a9b52', '#50d475')
CELL = 10
PITCH = 13
GRID_Y = 77
ARTBOARD_WIDTH = 832
REVEAL_START = 9.0
REVEAL_LAST = 11.88
COUNT_PATTERN = re.compile(r'(No|[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+) contributions? on .+')


@dataclass(frozen=True)
class ContributionDay:
    date: date
    level: int
    count: int


class CalendarHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = {}
        self.counts = {}
        self.tooltip_id = None
        self.tooltip_text = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'td' and 'ContributionCalendar-day' in attrs.get('class', '').split():
            cell_id = attrs.get('id')
            raw_date = attrs.get('data-date', '')
            raw_level = attrs.get('data-level')
            day = date.fromisoformat(raw_date)
            if not cell_id or cell_id in self.cells or raw_date != day.isoformat():
                raise ValueError('GitHub returned an invalid or duplicate calendar cell.')
            if raw_level not in ('0', '1', '2', '3', '4'):
                raise ValueError(f'GitHub returned an invalid contribution level for {day}.')
            self.cells[cell_id] = (day, int(raw_level))
        if tag == 'tool-tip':
            target = attrs.get('for', '')
            if target.startswith('contribution-day-component-'):
                self.tooltip_id = target
                self.tooltip_text = []

    def handle_data(self, data):
        if self.tooltip_id is not None:
            self.tooltip_text.append(data)

    def handle_endtag(self, tag):
        if tag != 'tool-tip' or self.tooltip_id is None:
            return
        text = ' '.join(''.join(self.tooltip_text).split())
        match = COUNT_PATTERN.fullmatch(text)
        if not match or self.tooltip_id in self.counts:
            raise ValueError(f'GitHub returned an invalid contribution tooltip for {self.tooltip_id}.')
        value = match[1]
        self.counts[self.tooltip_id] = 0 if value == 'No' else int(value.replace(',', ''))
        self.tooltip_id = None
        self.tooltip_text = []


def parse_contributions(page):
    parser = CalendarHTML()
    parser.feed(page)
    parser.close()

    if not 365 <= len(parser.cells) <= 367 or parser.cells.keys() != parser.counts.keys():
        raise ValueError('GitHub did not return a full year of calendar dates and counts.')

    days = sorted(
        (ContributionDay(day, level, parser.counts[cell_id])
         for cell_id, (day, level) in parser.cells.items()),
        key=lambda item: item.date,
    )
    for index, day in enumerate(days):
        if day.date != days[0].date + timedelta(days=index):
            raise ValueError('GitHub returned missing or duplicate contribution dates.')
        if bool(day.level) != bool(day.count):
            raise ValueError(f'GitHub returned conflicting level and count for {day.date}.')
    return days


def fetch_contributions(username):
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?', username):
        raise ValueError('Use a valid GitHub username.')
    request = Request(
        f'https://github.com/users/{username}/contributions',
        headers={'User-Agent': 'profile-calendar-updater', 'Accept-Language': 'en-US'},
    )
    with urlopen(request, timeout=30) as response:
        if response.headers.get_content_type() != 'text/html':
            raise ValueError('GitHub did not return the contribution calendar as HTML.')
        return parse_contributions(response.read().decode('utf-8'))


def render_calendar(days):
    first, last = days[0].date, days[-1].date
    first_row = (first.weekday() + 1) % 7
    columns = (first_row + len(days) + 6) // 7
    grid_width = (columns - 1) * PITCH + CELL
    grid_x = (ARTBOARD_WIDTH - grid_width) // 2
    right = grid_x + grid_width

    labels = []
    tiles = []
    active_count = sum(day.level > 0 for day in days)
    total = sum(day.count for day in days)
    last_month_column = -4
    active_index = 0
    for index, day in enumerate(days):
        column, row = divmod(first_row + index, 7)
        x = grid_x + column * PITCH
        y = GRID_Y + row * PITCH
        if (index == 0 or day.date.day == 1) and column - last_month_column >= 3:
            month = month_abbr[day.date.month]
            labels.append(f'<text x="{x}" y="67">{month}</text>')
            last_month_column = column

        tile = (f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" '
                f'fill="{COLORS[day.level]}" data-date="{day.date}"')
        if day.level:
            delay = REVEAL_START + (REVEAL_LAST - REVEAL_START) * active_index / max(active_count - 1, 1)
            noun = 'contribution' if day.count == 1 else 'contributions'
            tiles.append(f'{tile} class="day" style="--tier:{COLORS[day.level]};--on:{delay:.3f}s">'
                         f'<title>{day.date} · {day.count:,} {noun}</title></rect>')
            active_index += 1
        else:
            tiles.append(f'{tile}/>')

    for row, label in ((1, 'Mon'), (3, 'Wed'), (5, 'Fri')):
        labels.append(f'<text x="{grid_x - 9}" y="{GRID_Y + row * PITCH + 8}" '
                      f'text-anchor="end">{label}</text>')

    range_text = f'{month_abbr[first.month]} {first.day}, {first.year} – {month_abbr[last.month]} {last.day}, {last.year}'
    footer = [
        f'<text class="calendar-range" x="{grid_x}" y="183">{range_text}</text>',
        f'<text x="{ARTBOARD_WIDTH // 2}" y="183" text-anchor="middle" fill="#d6e9dc">'
        f'{total:,} contributions · {active_count} active days</text>',
        f'<text class="legend-less" x="{right - 130}" y="183">Less</text>',
        f'<text class="legend-more" x="{right}" y="183" text-anchor="end">More</text>',
    ]
    for level, fill in enumerate(COLORS):
        footer.append(f'<rect x="{right - 98 + level * PITCH}" y="174" '
                      f'width="{CELL}" height="{CELL}" rx="2" fill="{fill}" stroke="#2c4235"/>')

    return [
        '<g class="calendar-labels" fill="#9db3a5" font-family="ui-monospace, Consolas, monospace" font-size="10">',
        *(f'  {line}' for line in labels + tiles + footer),
        '</g>',
    ]


def replace_calendar(source, lines):
    if source.count(START_MARKER) != 1 or source.count(END_MARKER) != 1:
        raise ValueError('The SVG must contain exactly one pair of contribution markers.')
    before, _, previous = source.partition(START_MARKER)
    _, closing, after = previous.partition(END_MARKER)
    if not closing:
        raise ValueError('The contribution markers in the SVG are out of order.')

    indentation = re.search(r'[ \t]*\Z', before)[0]
    newline = '\r\n' if '\r\n' in source else '\n'
    body = ''.join(newline + indentation + '  ' + line for line in lines)
    return before + START_MARKER + body + newline + indentation + END_MARKER + after


def refresh(username, asset):
    previous = asset.read_bytes().decode('utf-8')
    days = fetch_contributions(username)
    updated = replace_calendar(previous, render_calendar(days))
    if updated == previous:
        return False

    output = NamedTemporaryFile(dir=asset.parent, delete=False)
    temporary = Path(output.name)
    try:
        with output:
            output.write(updated.encode('utf-8'))
        temporary.replace(asset)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('username', help='GitHub username whose public calendar to replay')
    args = parser.parse_args()
    try:
        changed = refresh(args.username, ASSET)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Could not refresh the contribution calendar. {error}\n')
    print('Updated the contribution calendar.' if changed else 'The contribution calendar is current.')


if __name__ == '__main__':
    main()
