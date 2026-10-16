import datetime as dt
import json
import lzma
import struct
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from duka_raw90 import day,REC

class Raw90HourlyTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.date=dt.date(2026,10,16)
        self.record=lzma.compress(REC.pack(100,2600200,2600100,1.0,1.0))

    def test_complete_http_attempt_manifest(self):
        with patch("duka_raw90.get",return_value=self.record):
            r=day(self.date,self.root,"XAUUSD")
        self.assertTrue(r["hourly_attempts_recorded"])
        self.assertEqual(r["ticks"],24)
        side=json.loads((self.root/"2026-10-16.hours.json").read_text())
        self.assertEqual(len(side["hours"]),24)
        self.assertTrue(side["complete_http_attempts"])
        self.assertFalse(side["market_hours_complete_verified"])
        with patch("duka_raw90.get",side_effect=AssertionError("should cache")):
            cached=day(self.date,self.root,"XAUUSD")
        self.assertTrue(cached["hourly_attempts_recorded"])

    def test_transient_network_error_never_persists_partial_day(self):
        def getter(url):
            if "01h_ticks" in url:
                raise TimeoutError("simulated network failure")
            return self.record
        with patch("duka_raw90.get",side_effect=getter):
            with self.assertRaisesRegex(RuntimeError,"fetch/decode failed"):
                day(self.date,self.root,"XAUUSD")
        self.assertFalse((self.root/"2026-10-16.csv").exists())
        self.assertFalse((self.root/"2026-10-16.tmp").exists())

    def test_404_is_recorded_not_assumed_verified_market(self):
        def getter(url):
            if "00h_ticks" in url:
                raise urllib.error.HTTPError(url,404,"missing",{},None)
            return self.record
        with patch("duka_raw90.get",side_effect=getter):
            day(self.date,self.root,"XAUUSD")
        report=json.loads((self.root/"2026-10-16.hours.json").read_text())
        self.assertEqual(report["hours"][0]["status"],"HTTP_NOT_FOUND")
        self.assertFalse(report["market_hours_complete_verified"])

    def test_legacy_cache_is_unverified(self):
        (self.root/"2026-10-16.csv").write_text("time,bid,ask,bid_size,ask_size\n"+"x,1,2,3,4\n"*10)
        r=day(self.date,self.root,"XAUUSD")
        self.assertTrue(r["legacy_unverified"])
        self.assertFalse(r["hourly_attempts_recorded"])

if __name__=="__main__":
    unittest.main()
