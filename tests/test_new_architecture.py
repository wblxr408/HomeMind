"""Test suite for architecture upgrade modules: MCP, Agent, Context, Distributed."""

import pytest
import asyncio


class TestMCPModule:
    """MCP Server, Client, Tools, Handlers."""

    def test_mcp_tools_defined(self):
        from core.mcp import HOMEMIND_TOOLS
        assert len(HOMEMIND_TOOLS) == 9
        names = [t.name for t in HOMEMIND_TOOLS]
        assert "device_control" in names
        assert "trigger_scene" in names
        assert "query_context" in names
        assert "info_query" in names
        assert "kb_query" in names
        assert "kb_add" in names
        assert "nl_to_scene_rule" in names
        assert "rule_list" in names
        assert "rule_toggle" in names

    def test_mcp_tool_schemas(self):
        from core.mcp import HOMEMIND_TOOLS
        dc = next(t for t in HOMEMIND_TOOLS if t.name == "device_control")
        assert dc.inputSchema["type"] == "object"
        assert "device" in dc.inputSchema["properties"]
        assert "action" in dc.inputSchema["properties"]
        assert dc.inputSchema["properties"]["device"]["enum"] == ["空调", "灯光", "电视", "热水器", "风扇", "音响", "窗户"]

    def test_mcp_server_instance(self):
        from core.mcp import mcp_server
        assert mcp_server is not None
        assert mcp_server.name == "homemind"

    def test_handler_registration(self):
        from core.mcp.tools import register_tool_handler, get_tool_handler
        handler_called = []

        def test_handler(args):
            handler_called.append(args)
            return "ok"

        register_tool_handler("test_tool", test_handler)
        assert get_tool_handler("test_tool") is test_handler
        assert get_tool_handler("nonexistent") is None


class TestAgentModule:
    """Event Bus, Coordinator, Specialist Agents."""

    def test_event_bus_publish_subscribe(self):
        from core.agent.bus import EventBus, Event, EventType

        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.USER_QUERY, handler)
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(type=EventType.USER_QUERY, source="test", payload={"q": "hello"}))
        )
        assert len(received) == 1
        assert received[0].payload["q"] == "hello"

    def test_event_bus_unsubscribe(self):
        from core.agent.bus import EventBus, EventType

        bus = EventBus()
        called = []

        def h(e):
            called.append(e)

        bus.subscribe(EventType.DEVICE_STATE_CHANGE, h)
        assert bus.get_subscriber_count(EventType.DEVICE_STATE_CHANGE) == 1
        bus.unsubscribe(EventType.DEVICE_STATE_CHANGE, h)
        assert bus.get_subscriber_count(EventType.DEVICE_STATE_CHANGE) == 0

    def test_event_types(self):
        from core.agent.bus import EventType
        assert EventType.USER_QUERY.value == "user_query"
        assert EventType.DEVICE_STATE_CHANGE.value == "device_state_change"
        assert EventType.SCENE_ACTIVATED.value == "scene_activated"
        assert EventType.PEER_MESSAGE.value == "peer_message"

    def test_agent_response(self):
        from core.agent.base import AgentResponse
        resp = AgentResponse(success=True, content="done", agent_name="TestAgent")
        assert resp.success is True
        assert resp.content == "done"
        d = resp.to_dict()
        assert d["agent_name"] == "TestAgent"
        assert d["success"] is True


class TestContextCompressor:
    """Query-Conditioned Context Compression."""

    def test_token_estimation(self):
        from core.context.compressor import estimate_tokens

        # 英文经验估算
        en_tokens = estimate_tokens("hello world")
        assert en_tokens >= 2

        # 中文估算
        zh_tokens = estimate_tokens("你好世界")
        assert zh_tokens >= 2

    def test_context_block(self):
        from core.context.compressor import ContextBlock

        block = ContextBlock(content="测试内容", source="rag", value_score=0.8)
        assert block.content == "测试内容"
        assert block.source == "rag"
        assert block.value_score == 0.8
        assert block.token_count > 0

    def test_token_budget_fit(self):
        from core.context.compressor import ContextBlock, TokenBudget

        blocks = [
            ContextBlock(content="a" * 100, source="rag", value_score=0.5),
            ContextBlock(content="b" * 100, source="rag", value_score=0.5),
            ContextBlock(content="c" * 100, source="rag", value_score=0.5),
        ]
        for b in blocks:
            b.token_count = 2

        budget = TokenBudget(max_tokens=4)
        result = budget.fit(blocks)
        assert len(result.kept) == 2
        assert len(result.discarded) == 1
        assert result.kept_tokens == 4

    def test_soft_selector_score(self):
        from core.context.compressor import ContextBlock, SoftSelector

        selector = SoftSelector()
        blocks = [
            ContextBlock(content="空调", source="rag", value_score=0.5),
            ContextBlock(content="天气", source="rag", value_score=0.5),
        ]
        scored = selector.score("空调温度", blocks)
        assert len(scored) == 2
        # "空调" 应该排在 "天气" 前面（相似度更高）
        assert scored[0][0] >= scored[1][0]

    def test_compress_pass_through(self):
        from core.context.compressor import ContextCompressor, ContextBlock

        compressor = ContextCompressor(max_tokens=4096)
        blocks = [ContextBlock(content="short content", source="test")]
        result = asyncio.get_event_loop().run_until_complete(
            compressor.compress("test query", blocks, max_tokens=4096)
        )
        assert "short content" in result


class TestHierarchicalKV:
    """Three-tier hierarchical KV Store."""

    def test_kv_get_set(self):
        from core.memory.hierarchical_kv import HierarchicalKV
        import tempfile, os

        tmp = tempfile.mktemp(suffix=".db")
        kv = HierarchicalKV(l2_path=tmp)

        asyncio.get_event_loop().run_until_complete(kv.set("key1", {"a": 1}))
        val = asyncio.get_event_loop().run_until_complete(kv.get("key1"))
        assert val == {"a": 1}

        # 覆盖写入
        asyncio.get_event_loop().run_until_complete(kv.set("key1", {"a": 2}))
        val = asyncio.get_event_loop().run_until_complete(kv.get("key1"))
        assert val == {"a": 2}

        # 不存在的 key
        val = asyncio.get_event_loop().run_until_complete(kv.get("nonexistent", default="default_val"))
        assert val == "default_val"

        os.unlink(tmp)

    def test_kv_delete(self):
        from core.memory.hierarchical_kv import HierarchicalKV
        import tempfile, os

        tmp = tempfile.mktemp(suffix=".db")
        kv = HierarchicalKV(l2_path=tmp)

        asyncio.get_event_loop().run_until_complete(kv.set("del_key", "value"))
        asyncio.get_event_loop().run_until_complete(kv.delete("del_key"))
        val = asyncio.get_event_loop().run_until_complete(kv.get("del_key"))
        assert val is None

        # Close L2 connection before cleanup
        import sqlite3
        try:
            conn = sqlite3.connect(tmp)
            conn.close()
        except Exception:
            pass
        try:
            os.unlink(tmp)
        except Exception:
            pass

    def test_kv_stats(self):
        from core.memory.hierarchical_kv import HierarchicalKV
        import tempfile, os

        tmp = tempfile.mktemp(suffix=".db")
        kv = HierarchicalKV(l2_path=tmp)
        asyncio.get_event_loop().run_until_complete(kv.set("k1", "v1"))
        asyncio.get_event_loop().run_until_complete(kv.set("k2", "v2"))

        stats = kv.get_stats()
        assert stats["l1_entries"] == 2
        assert stats["l1_size_bytes"] > 0

        # Close L2 connection before cleanup
        import sqlite3
        try:
            conn = sqlite3.connect(tmp)
            conn.close()
        except Exception:
            pass
        try:
            os.unlink(tmp)
        except Exception:
            pass


class TestA2AProtocol:
    """A2A v1.0 protocol."""

    def test_agent_card(self):
        from core.agent.protocols.a2a import AgentCard
        card = AgentCard(
            name="HomeMind-DeviceAgent",
            description="Device control agent",
            url="http://192.168.1.101:8765",
            capabilities=["device_control", "status_query"],
        )
        d = card.to_dict()
        assert d["name"] == "HomeMind-DeviceAgent"
        assert "device_control" in d["capabilities"]

    def test_task_status(self):
        from core.agent.protocols.a2a import Task, TaskStatus
        task = Task(agent_name="test")
        assert task.status == TaskStatus.SUBMITTED
        task.status = TaskStatus.WORKING
        assert task.status == TaskStatus.WORKING

    def test_a2a_protocol(self):
        from core.agent.protocols.a2a import A2AProtocol, AgentCard
        proto = A2AProtocol()
        card = proto.create_agent_card(
            name="TestAgent",
            description="Test",
            url="http://localhost:8765",
            capabilities=["test"],
        )
        assert card.name == "TestAgent"
        proto.register_local_agent(card)
        assert proto.get_agent_card("TestAgent") is not None


class TestDistributed:
    """mDNS Discovery and Mesh Transport."""

    def test_peer_node(self):
        from core.distributed import PeerNode

        peer = PeerNode(
            node_id="hm-test-01",
            host="192.168.1.100",
            port=8765,
            capabilities=["agent", "gateway"],
        )
        assert peer.endpoint() == "http://192.168.1.100:8765"
        d = peer.to_dict()
        assert d["node_id"] == "hm-test-01"
        assert "agent" in d["capabilities"]

    def test_mesh_message(self):
        from core.distributed import MeshMessage

        msg = MeshMessage(
            from_node="node1",
            to_node="node2",
            type="event",
            action="device_state",
            payload={"device": "空调", "status": "on"},
        )
        assert msg.from_node == "node1"
        assert msg.to_node == "node2"
        assert msg.payload["device"] == "空调"

    def test_local_discovery_init(self):
        from core.distributed import LocalDiscovery

        d = LocalDiscovery(
            node_id="hm-test",
            capabilities=["agent"],
            port=9000,
        )
        assert d.node_id == "hm-test"
        assert d.port == 9000
        assert "agent" in d.capabilities
