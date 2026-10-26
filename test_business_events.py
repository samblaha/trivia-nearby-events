import unittest
from datetime import datetime, timezone
from business_events import occurrences, merge_business_events

class BusinessEventsTests(unittest.TestCase):
    def row(self, **kw):
        return dict(id='test',title='Trivia',venue_name='Pub',address='123 Main St',city='Columbus, OH',weekday=4,start_time='19:00:00',timezone='America/New_York',first_date='2026-10-01',last_date='2026-12-01',description='A quiz',booking_url='',category='general',sponsored=True,**kw)
    def test_week_and_tonight(self):
        now=datetime(2026,10,22,20,tzinfo=timezone.utc)
        events=occurrences([self.row()], 'tonight', now)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['start_date'],'2026-10-22T19:00:00-04:00')
        self.assertEqual(len(occurrences([self.row()],'week',now)),1)
    def test_past_event_and_other_day_excluded(self):
        self.assertEqual(occurrences([self.row()],'tonight',datetime(2026,10,23,1,tzinfo=timezone.utc)),[])
    def test_dst_uses_venue_timezone(self):
        events=occurrences([self.row()],'month',datetime(2026,10,23,20,tzinfo=timezone.utc))
        self.assertTrue(events[0]['start_date'].endswith('-04:00'))
        self.assertTrue(events[-1]['start_date'].endswith('-05:00'))
    def test_schedule_end_limits_occurrences(self):
        row=self.row();row['last_date']='2026-10-23'
        self.assertEqual(occurrences([row],'month',datetime(2026,10,24,tzinfo=timezone.utc)),[])
    def test_merge_does_not_mutate_cached_response(self):
        body={'events':[{'id':'google','title':'Other'}],'count':1}
        events=occurrences([self.row()],'week',datetime(2026,10,22,20,tzinfo=timezone.utc))
        merged=merge_business_events(body,events)
        self.assertEqual(merged['count'],2);self.assertEqual(body['count'],1)
        self.assertTrue(merged['events'][0]['sponsored'])
if __name__=='__main__': unittest.main()
