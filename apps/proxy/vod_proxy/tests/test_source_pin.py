from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.proxy.vod_proxy.source_pin import claim_source_pin, read_source_pin
from apps.proxy.vod_proxy.tests.test_vod_lock_contention import LockAwareFakeRedis


def source(profile_id=1, url="http://provider.example/first.mkv", content_id="movie"):
    return {"content_type": "movie", "content_id": content_id,
            "profile_id": profile_id, "stream_url": url}


class SourcePinTests(TestCase):
    def setUp(self):
        self.redis = LockAwareFakeRedis()

    def test_concurrent_workers_choose_one_file(self):
        with ThreadPoolExecutor(max_workers=8) as workers:
            pins = list(workers.map(lambda i: claim_source_pin(
                self.redis, "session", "movie", "movie",
                source(i + 1, f"http://provider.example/{i}.mkv")), range(32)))
        self.assertTrue(all(pin == pins[0] for pin in pins))

    def test_viewers_keep_independent_sources(self):
        first = claim_source_pin(self.redis, "viewer-a", "movie", "movie", source())
        second = claim_source_pin(self.redis, "viewer-b", "movie", "movie", source(2))
        self.assertNotEqual(first["profile_id"], second["profile_id"])

    def test_seek_keeps_source_after_connection_cleanup(self):
        original = claim_source_pin(self.redis, "session", "movie", "movie", source())
        self.redis.delete("vod_persistent_connection:session")
        self.assertEqual(read_source_pin(self.redis, "session", "movie", "movie"), original)

    def test_legacy_session_keeps_its_existing_source(self):
        self.redis.hset("vod_persistent_connection:session", mapping={
            "content_obj_type": "movie", "content_uuid": "movie",
            "m3u_profile_id": 7, "stream_url": "http://provider.example/legacy.mkv"})
        pin = read_source_pin(self.redis, "session", "movie", "movie")
        self.assertEqual(pin["profile_id"], "7")
        self.assertEqual(pin["stream_url"], "http://provider.example/legacy.mkv")

    def test_session_cannot_be_reused_for_different_content(self):
        claim_source_pin(self.redis, "session", "movie", "movie", source())
        with self.assertRaises(ValueError):
            read_source_pin(self.redis, "session", "episode", "episode")

    def test_broken_pin_does_not_fall_back(self):
        self.redis.set("vod_source_pin:session", "invalid json")
        with self.assertRaises(ValueError):
            read_source_pin(self.redis, "session", "movie", "movie")


class PinnedSelectionTests(TestCase):
    def setUp(self):
        self.redis = LockAwareFakeRedis()
        self.profile = MagicMock(id=1, m3u_account_id=1)
        self.content = MagicMock()

    def test_head_then_seek_ignores_changed_candidate_order_and_capacity(self):
        from apps.proxy.vod_proxy.views import _select_vod_stream
        candidate = MagicMock(m3u_account_id=1)
        with patch("apps.proxy.vod_proxy.views._get_content_and_relation", return_value=(
            self.content, candidate, [candidate])), patch(
            "core.utils.RedisClient.get_client", return_value=self.redis), patch(
            "apps.proxy.vod_proxy.views._get_m3u_profile", return_value=(self.profile, 0)) as pick, patch(
            "apps.proxy.vod_proxy.views._build_vod_stream_url", return_value=source()["stream_url"]) as build, patch(
            "apps.proxy.vod_proxy.views.M3UAccountProfile.objects") as profiles:
            profiles.select_related.return_value.get.return_value = self.profile
            head = _select_vod_stream("movie", "movie", session_id="session")
            pick.return_value = None  # Pool fills before the next Range request.
            build.return_value = "http://provider.example/different-layout.mkv"
            seek = _select_vod_stream("movie", "movie", session_id="session")
        self.assertEqual(head["final_stream_url"], seek["final_stream_url"])
        pick.assert_called_once()
        build.assert_called_once()

    def test_disabled_pinned_profile_fails_without_provider_fallback(self):
        from apps.proxy.vod_proxy.views import _select_vod_stream
        claim_source_pin(self.redis, "session", "movie", "movie", source())
        with patch("apps.proxy.vod_proxy.views._get_content_and_relation", return_value=(
            self.content, MagicMock(), [])), patch(
            "core.utils.RedisClient.get_client", return_value=self.redis), patch(
            "apps.proxy.vod_proxy.views._selection_from_pin", side_effect=ValueError("disabled")), patch(
            "apps.proxy.vod_proxy.views._get_m3u_profile") as pick:
            self.assertIsNone(_select_vod_stream("movie", "movie", session_id="session"))
        pick.assert_not_called()

    def test_lazy_episode_failover_runs_before_pin_and_retries_once(self):
        from apps.proxy.vod_proxy.views import _select_vod_stream
        candidate = MagicMock(m3u_account_id=1)
        with patch("apps.proxy.vod_proxy.views._get_content_and_relation", return_value=(
            self.content, candidate, [candidate])), patch(
            "core.utils.RedisClient.get_client", return_value=self.redis), patch(
            "apps.proxy.vod_proxy.views._get_m3u_profile", side_effect=[None, (self.profile, 0)]), patch(
            "apps.proxy.vod_proxy.views._lazy_fetch_episode_providers", return_value=True) as refresh, patch(
            "apps.proxy.vod_proxy.views._build_vod_stream_url", return_value=source()["stream_url"]), patch(
            "apps.proxy.vod_proxy.views.M3UAccountProfile.objects") as profiles:
            profiles.select_related.return_value.get.return_value = self.profile
            result = _select_vod_stream("episode", "episode", session_id="session")
        self.assertEqual(result["final_stream_url"], source()["stream_url"])
        refresh.assert_called_once_with("episode")
        self.assertIsNotNone(read_source_pin(self.redis, "session", "episode", "episode"))

    def test_redis_failure_cannot_select_an_unpinned_source(self):
        from apps.proxy.vod_proxy.views import _select_vod_stream
        with patch("apps.proxy.vod_proxy.views._get_content_and_relation", return_value=(
            self.content, MagicMock(), [])), patch(
            "core.utils.RedisClient.get_client", return_value=None), patch(
            "apps.proxy.vod_proxy.views._get_m3u_profile") as pick:
            self.assertIsNone(_select_vod_stream("movie", "movie", session_id="session"))
        pick.assert_not_called()


class ViewerIsolationTests(TestCase):
    def test_same_ip_and_agent_cannot_adopt_another_viewer_session(self):
        from apps.proxy.vod_proxy.multi_worker_connection_manager import MultiWorkerVODConnectionManager
        manager = MultiWorkerVODConnectionManager.__new__(MultiWorkerVODConnectionManager)
        manager.redis_client = LockAwareFakeRedis()
        manager.worker_id = "test-worker"
        with patch.object(manager, "find_matching_idle_session", return_value="viewer-a"), patch.object(
            manager, "_check_and_reserve_profile_slot", return_value=False), patch(
            "apps.proxy.vod_proxy.multi_worker_connection_manager.RedisBackedVODConnection") as connection:
            connection.return_value._get_connection_state.return_value = None
            response = manager.stream_content_with_session(
                session_id="viewer-b", content_obj=MagicMock(),
                stream_url=source()["stream_url"], m3u_profile=MagicMock(),
                client_ip="10.0.10.113", client_user_agent="Jellyfin", request=MagicMock())
        self.assertEqual(response.status_code, 429)
        connection.assert_called_once_with("viewer-b", manager.redis_client)
