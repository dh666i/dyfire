import unittest

from core.avatars import avatar_from_user_info, safe_avatar_url
from core.web_chat import UserInfoCollector


URL = "https://p3.douyinpic.com/aweme/avatar.jpeg?token=example"


class AvatarTests(unittest.IsolatedAsyncioTestCase):
    def test_only_approved_https_cdn_urls_are_allowed(self):
        self.assertEqual(URL, safe_avatar_url(URL))
        for value in (None, {}, "http://p3.douyinpic.com/a", "javascript:alert(1)",
                      "https://douyinpic.com.evil.test/a", "https://127.0.0.1/a",
                      "https://user@p3.douyinpic.com/a", "https://p3.douyinpic.com:8080/a",
                      "https://p3.douyinpic.com:bad/a", "https://p3.douyinpic.com/a\npath",
                      "https://p3.douyinpic.com/\\evil", "https://[malformed/a"):
            with self.subTest(value=value):
                self.assertIsNone(safe_avatar_url(value))

    def test_structured_candidates_fallback_and_missing_avatar(self):
        self.assertEqual(URL, avatar_from_user_info({"avatar_thumb": {"url_list": ["bad", URL]}}))
        self.assertEqual(URL, avatar_from_user_info({"avatar_thumb": None, "avatar_medium": {"url_list": [URL]}}))
        self.assertEqual(URL, avatar_from_user_info({"avatar_url": URL}))
        self.assertIsNone(avatar_from_user_info({"avatar_thumb": {"url_list": "bad"}}))

    async def test_collector_keeps_avatar_when_later_response_omits_it(self):
        class Response:
            url = "https://www.douyin.com/aweme/v1/web/im/user/info/"
            status = 200
            item = {"sec_uid": "stable", "nickname": "Friend", "avatar_thumb": {"url_list": [URL]}}

            async def json(self):
                return {"data": [self.item]}

        response = Response()
        collector = UserInfoCollector()
        await collector.handle_response(response)
        response.item = {"sec_uid": "stable", "nickname": "Renamed"}
        await collector.handle_response(response)
        self.assertEqual({"stable": URL}, collector.avatar_urls)
        self.assertEqual("Renamed", collector.get("stable").nickname)

