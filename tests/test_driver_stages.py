from types import SimpleNamespace
from django.test import SimpleTestCase
from fleet.trip_stages import driver_stage_progress, driver_journey_progress


class DriverStagesTests(SimpleTestCase):
    def trip(self, status='planned', accepted=False, stop=None, route=True):
        return SimpleNamespace(status=status, requires_acceptance=not accepted,
                               route_enabled=route, current_stop=stop)

    def test_simple_route_has_six_stages_and_one_confirmed_current_stage(self):
        for status, accepted, key in [('planned',False,'planned'), ('planned',True,'accepted'),
                                      ('loading',True,'loading'), ('road',True,'road'),
                                      ('unloading',True,'unloading'), ('completed',True,'completed')]:
            with self.subTest(status=status, accepted=accepted):
                steps=driver_stage_progress(self.trip(status,accepted))
                self.assertEqual(len(steps),6)
                self.assertEqual([s['key'] for s in steps if s['current']],[key])
                self.assertFalse(any(s['current'] and s['done'] for s in steps))
                current=next(i for i,s in enumerate(steps) if s['current'])
                self.assertTrue(all(s['done'] for s in steps[:current]))
                self.assertTrue(all(not s['done'] for s in steps[current:]))

    def test_repeated_load_and_unload_use_current_stop_operation(self):
        for kind,label in [('load','Завантаження'),('unload','Розвантаження')]:
            stop=SimpleNamespace(kind=kind,state='arrived')
            steps=driver_stage_progress(self.trip('loading' if kind=='load' else 'unloading',True,stop),False)
            self.assertEqual([s['label'] for s in steps if s['current']],[label])
            self.assertFalse(steps[-1]['done'])
            stop.state='pending'
            steps=driver_stage_progress(self.trip('road',True,stop),False)
            self.assertEqual([s['key'] for s in steps if s['current']],['road'])
            self.assertFalse(steps[3]['done'])

    def test_queue_acceptance_does_not_imply_driving(self):
        steps=driver_stage_progress(self.trip('queued',True))
        self.assertEqual([s['key'] for s in steps if s['current']],['accepted'])

    def test_no_active_trip(self):
        self.assertEqual(driver_stage_progress(None),[])
        self.assertEqual(driver_stage_progress(self.trip('cancelled')),[])

    def test_simple_journey_marks_only_confirmed_work(self):
        for status,accepted,current,done in [('planned',False,None,[]),('planned',True,'loading',[]),('loading',True,'loading',[]),('road',True,'road',['loading']),('unloading',True,'unloading',['loading','road']),('completed',True,'completed',['loading','road','unloading'])]:
            with self.subTest(status=status,accepted=accepted):
                steps=driver_journey_progress(self.trip(status,accepted))
                self.assertEqual([s['key'] for s in steps if s['current']],[current] if current else [])
                self.assertEqual([s['key'] for s in steps if s['done']],done)

    def test_multi_stop_journey_uses_current_point_cycle(self):
        stop=SimpleNamespace(kind='load',state='arrived')
        steps=driver_journey_progress(self.trip('loading',True,stop),False)
        self.assertEqual([s['key'] for s in steps if s['current']],['operation'])
