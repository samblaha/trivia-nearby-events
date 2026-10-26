"""Public, column-limited Supabase feed for venue-managed trivia nights.
No service-role key is needed by this read-only adapter.
"""
from datetime import datetime, date, time, timedelta, timezone
import json
import logging
import os
import re
import urllib.request
from zoneinfo import ZoneInfo

SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://jtzjfyjgzkdldbcewrdp.supabase.co')
PUBLIC_KEY = os.getenv('SUPABASE_PUBLISHABLE_KEY', 'sb_publishable_EPwtwPsPoOQqT75QUc6QSA_brapMkI3')

def occurrences(rows, when='week', now=None):
    now = now or datetime.now(timezone.utc)
    output = []
    for row in rows:
        try:
            tz = ZoneInfo(row['timezone'])
            local_now = now.astimezone(tz)
            start = max(date.fromisoformat(row['first_date']), local_now.date())
            end = min(date.fromisoformat(row['last_date']), local_now.date() + timedelta(days={'tonight': 0, 'week': 6, 'month': 30, 'any': 30}.get(when, 6)))
            start += timedelta(days=(row['weekday'] - (start.weekday() + 1) % 7) % 7)
            while start <= end:
                at = datetime.combine(start, time.fromisoformat(row['start_time']), tzinfo=tz)
                if at >= local_now:
                    output.append({
                        'id': f"business-{row['id']}-{start.isoformat()}",
                        'series_id': f"business-{row['id']}", 'title': row['title'],
                        'venue_name': row['venue_name'], 'when': at.strftime('%a, %b %d, %Y, %I:%M %p'),
                        'start_date': at.isoformat(),
                        'address': [f"{row['venue_name']}, {row['address']}, {row['city']}"],
                        'type': row['category'], 'description': row['description'],
                        'sponsored': bool(row['sponsored']),
                        'ticket_info': [{'source': 'Venue / host', 'link': row['booking_url']}] if row['booking_url'] else [],
                    })
                start += timedelta(days=7)
        except (KeyError, ValueError, TypeError):
            logging.warning('Skipped invalid business event row')
    # Keep paid placement ahead of organic listings; the app rotates sponsored ties.
    output.sort(key=lambda e: (not e['sponsored'], e['start_date'], e['id']))
    return output

def search_business_events(location, when='week'):
    # City, state and Columbus ZIP searches; other exact city labels remain usable.
    city = location.strip()
    if re.fullmatch(r'columbus(?:\s*,?\s*(?:oh|ohio))?', city, re.I) or (len(city) == 5 and city.isdigit() and city.startswith('432')):
        city = 'Columbus, OH'
    request = urllib.request.Request(SUPABASE_URL + '/rest/v1/rpc/business_event_feed',
        data=json.dumps({'search_city': city}).encode(),
        headers={'apikey': PUBLIC_KEY, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return occurrences(json.load(response), when)
    except Exception as exc:
        logging.warning('Business feed unavailable: %s', type(exc).__name__)
        return []

def merge_business_events(body, managed):
    # Prefer the direct schedule for the same venue and exact local start time.
    def key(event):
        venue = re.sub(r'[^a-z0-9]', '', event.get('venue_name', '').casefold())
        # Compare local wall times, since Google labels generally omit offsets.
        raw = event.get('start_date') or ''
        try:
            at = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            slot = at.strftime('%Y-%m-%d %H:%M')
        except ValueError:
            slot = None
            for fmt in ('%a, %b %d, %Y, %I:%M %p', '%b %d, %Y, %I:%M %p'):
                try:
                    slot = datetime.strptime(event.get('when') or '', fmt).strftime('%Y-%m-%d %H:%M')
                    break
                except ValueError:
                    pass
        # Do not collapse distinct events when a precise time is unavailable.
        return (venue, slot) if venue and slot else ('id', event.get('id'))
    managed_keys = {key(e) for e in managed}
    others = [e for e in body.get('events', []) if key(e) not in managed_keys]
    events = [e for e in managed if e['sponsored']] + others + [e for e in managed if not e['sponsored']]
    return {**body, 'events': events, 'count': len(events)}
