#!/usr/bin/env python3
"""
IP雷达去重切词 -- A2A HTTP 服务
接收原始热搜items, 执行词库去重分类, 返回带ip_status/matched_value/ipCategory的完整结果
"""

import json, base64, datetime, ssl, urllib.request, urllib.error, http.server, socketserver, os, sys, re
from urllib.parse import urlparse

# ============ Config ============
PORT = int(os.environ.get('PORT', '3000'))
# ================================

# ============ LCS ============
def lcs_length(a, b):
    """最长公共子序列长度"""
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                curr[j] = prev[j-1] + 1
            else:
                curr[j] = max(prev[j], curr[j-1])
        prev, curr = curr, prev
    return prev[n]


def hit_rate(keyword, cand):
    """LCS hit_rate"""
    if not cand:
        return 0.0
    lcs = lcs_length(keyword.lower(), cand.lower())
    return lcs / len(cand)


# ============ 分类逻辑 ============
ABBREV_SET = {'lol', 'ti', 'kpl', 'lpl', 'dota', 'csgo', 'cs2', 'pubg', 'nba', 'cmb'}

def is_embedded(keyword, cand):
    """检测短候选串是否嵌在更长词里"""
    if len(cand) > 2:
        return False
    idx = keyword.lower().find(cand.lower())
    if idx < 0:
        return False
    # 检查前后字符
    before = keyword[idx-1:idx] if idx > 0 else ''
    after = keyword[idx+len(cand):idx+len(cand)+1] if idx + len(cand) < len(keyword) else ''
    before_is_word = before and (before.isalnum() or '\u4e00' <= before <= '\u9fff')
    after_is_word = after and (after.isalnum() or '\u4e00' <= after <= '\u9fff')
    return before_is_word or after_is_word


def norm_category(cat):
    """类目归一化"""
    if not cat:
        return None
    alias = {
        '明星': '明星艺人', '明星同款': '明星艺人', '明星/人物': '明星艺人', '明星人物': '明星艺人',
        '潮玩': '潮玩手办',
        '游戏': '游戏电竞',
        '动漫': '动漫二次元', '二次元': '动漫二次元', '卡通动漫': '动漫二次元',
        '动画': '动漫二次元', '漫画': '动漫二次元',
        '影视': '影视综', '影视综艺': '影视综',
        '体育': '文娱其他', '文化': '文娱其他', '建筑地标': '文娱其他',
        '地标': '文娱其他', '其他': '文娱其他',
    }
    nc = alias.get(cat)
    if nc:
        return nc
    valid = {'明星艺人', '潮玩手办', '动漫二次元', '影视综', '游戏电竞', '品牌', '文娱其他'}
    return cat if cat in valid else '文娱其他'


def classify_item(keyword, ips, kb_index, kb_records):
    """
    对单个keyword进行词库匹配分类
    返回: {ip_status, matched_value, ipCategory, match_type}
    """
    keyword_norm = keyword.strip() if keyword else ''
    if not keyword_norm:
        return {'ip_status': '新value', 'matched_value': None, 'ipCategory': None, 'match_type': 'none'}

    # Step 1: ips精确匹配
    for ip in (ips or []):
        ip_norm = ip.strip() if ip else ''
        if not ip_norm:
            continue
        # 在词库索引中查找
        if ip_norm in kb_index:
            rec = kb_records[kb_index[ip_norm]]
            return {
                'ip_status': '匹配成功',
                'matched_value': rec.get('标准词', ip_norm),
                'ipCategory': norm_category(rec.get('一级分类')),
                'match_type': 'ips_exact'
            }
        # 同义词匹配
        for std_word, idx in kb_index.items():
            rec = kb_records[idx]
            syns = rec.get('同义词', '')
            syn_list = [s.strip() for s in re.split(r'[|/,、;；]', syns) if s.strip()]
            if ip_norm in syn_list or ip_norm == std_word:
                return {
                    'ip_status': '匹配成功',
                    'matched_value': std_word,
                    'ipCategory': norm_category(rec.get('一级分类')),
                    'match_type': 'ips_exact'
                }

    # Step 2: 全量扫描
    best = None
    best_rate = 0.0
    best_len = 0

    for std_word, idx in kb_index.items():
        rec = kb_records[idx]
        candidates = [std_word]
        syns = rec.get('同义词', '')
        candidates.extend([s.strip() for s in re.split(r'[|/,、;；]', syns) if s.strip()])

        for cand in candidates:
            if not cand:
                continue
            rate = hit_rate(keyword_norm, cand)
            if rate >= 1.0:
                # 精确命中，直接返回
                cat = rec.get('一级分类', '')
                if cand.lower() in ABBREV_SET:
                    return {'ip_status': '需审核', 'matched_value': cand, 'ipCategory': norm_category(cat), 'match_type': 'abbrev_hit'}
                if len(cand) >= 3 or cand == keyword_norm:
                    return {'ip_status': '匹配成功', 'matched_value': std_word, 'ipCategory': norm_category(cat), 'match_type': 'substring_full'}
                if len(cand) <= 2 and norm_category(cat) == '明星艺人':
                    return {'ip_status': '匹配成功', 'matched_value': std_word, 'ipCategory': norm_category(cat), 'match_type': 'short_hit_person'}
                if len(cand) <= 2 and is_embedded(keyword_norm, cand):
                    return {'ip_status': '新value', 'matched_value': None, 'ipCategory': None, 'match_type': 'short_embedded'}
                if len(cand) <= 2:
                    return {'ip_status': '需审核', 'matched_value': std_word, 'ipCategory': norm_category(cat), 'match_type': 'short_hit'}
                return {'ip_status': '匹配成功', 'matched_value': std_word, 'ipCategory': norm_category(cat), 'match_type': 'substring_full'}
            elif rate > best_rate or (rate == best_rate and len(cand) > best_len):
                best = (std_word, rec, cand, rate)
                best_rate = rate
                best_len = len(cand)

    # Step 3: 部分匹配
    if best and best_rate >= 0.5:
        std_word, rec, cand, rate = best
        return {
            'ip_status': '需审核',
            'matched_value': std_word,
            'ipCategory': norm_category(rec.get('一级分类')),
            'match_type': 'partial'
        }

    return {'ip_status': '新value', 'matched_value': None, 'ipCategory': None, 'match_type': 'none'}


# ============ 内嵌词库 ============
# 由于沙箱环境无法读取本地文件, 这里内嵌一个小型词库作为兜底
# 实际使用时可以从请求中传入词库, 或从外部服务加载
EMBEDDED_KB = [
    {"标准词": "杨紫", "同义词": "", "一级分类": "明星艺人"},
    {"标准词": "肖战", "同义词": "", "一级分类": "明星艺人"},
    {"标准词": "王一博", "同义词": "", "一级分类": "明星艺人"},
    {"标准词": "赵丽颖", "同义词": "", "一级分类": "明星艺人"},
    {"标准词": "迪丽热巴", "同义词": "", "一级分类": "明星艺人"},
    {"标准词": "杨幂", "同义词": "", "一级分类": "明星艺人"},
    {"标准词": "/chrome", "同义词": "", "一级分类": "明星艺人"},
    {"标准词": "金鹰奖", "同义词": "", "一级分类": "影视综"},
    {"标准词": "白玉兰", "同义词": "", "一级分类": "影视综"},
    {"标准词": "原神", "同义词": "", "一级分类": "游戏电竞"},
    {"标准词": "崩坏星穹铁道", "同义词": "崩铁", "一级分类": "游戏电竞"},
    {"标准词": "王者荣耀", "同义词": "王者", "一级分类": "游戏电竞"},
    {"标准词": "chiikawa", "同义词": "", "一级分类": "潮玩手办"},
    {"标准词": "Labubu", "同义词": "拉布布", "一级分类": "潮玩手办"},
    {"标准词": "泡泡玛特", "同义词": "", "一级分类": "潮玩手办"},
    {"标准词": "哪吒", "同义词": "", "一级分类": "影视综"},
    {"标准词": "NBA", "同义词": "", "一级分类": "文娱其他"},
    {"标准词": "说唱巅峰对决", "同义词": "", "一级分类": "影视综"},
    {"标准词": "登陆少年", "同义词": "", "一级分类": "明星艺人"},
    {"标准词": "宋亚东", "同义词": "", "一级分类": "文娱其他"},
    {"标准词": "乌马尔", "同义词": "", "一级分类": "文娱其他"},
    {"标准词": "哈利波特", "同义词": "", "一级分类": "影视综"},
    {"标准词": "三丽鸥", "同义词": "sanrio", "一级分类": "潮玩手办"},
    {"标准词": "Jellycat", "同义词": "", "一级分类": "潮玩手办"},
    {"标准词": "迪士尼", "同义词": "Disney", "一级分类": "品牌"},
    {"标准词": "漫威", "同义词": "Marvel", "一级分类": "品牌"},
]


def build_kb_index(kb_data):
    """构建词库索引"""
    index = {}
    records = []
    for rec in kb_data:
        std = rec.get('标准词', '').strip()
        if not std:
            continue
        idx = len(records)
        records.append(rec)
        index[std] = idx
        # 同义词也加入索引
        syns = rec.get('同义词', '')
        for syn in re.split(r'[|/,、;；]', syns):
            syn = syn.strip()
            if syn and syn not in index:
                index[syn] = idx
    return index, records


# ============ 业务核心 ============
def process_dedup(items, kb_data=None):
    """
    主入口: 对items列表执行去重分类
    items: [{keyword, ips, score, platform, board, ...}]
    返回: [{keyword, ips, ip_status, matched_value, ipCategory, match_type, ...}]
    """
    kb = kb_data if kb_data else EMBEDDED_KB
    kb_index, kb_records = build_kb_index(kb)

    results = []
    for it in items:
        if not isinstance(it, dict):
            continue
        keyword = it.get('keyword', '')
        ips = it.get('ips', [])
        # 如果没有ips, 简单提取: 用keyword本身
        if not ips and keyword:
            ips = [keyword]

        cls = classify_item(keyword, ips, kb_index, kb_records)

        res = dict(it)
        res['ip_status'] = cls['ip_status']
        res['matched_value'] = cls['matched_value']
        res['ipCategory'] = cls['ipCategory']
        res['match_type'] = cls['match_type']
        res['hints'] = []
        results.append(res)

    return results


# ============ A2A Agent Card ============
AGENT_CARD = {
    "name": "IP雷达去重切词",
    "description": "接收原始热搜items, 执行词库去重分类(LCS hit_rate量化匹配), 输出带ip_status/matched_value/ipCategory的完整结果",
    "url": "",
    "version": "1.0.0",
    "provider": {"organization": "IP趋势雷达项目组"},
    "capabilities": {"streaming": True, "pushNotifications": False},
    "authentication": {"schemes": ["bearer"]},
    "skills": [
        {
            "id": "dedup-classify",
            "name": "去重分类",
            "description": "对热搜items执行词库去重分类, 返回带ip_status/matched_value/ipCategory/match_type的结果",
            "input": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "热搜items列表, 每个item至少含keyword字段",
                        "items": {
                            "type": "object",
                            "properties": {
                                "keyword": {"type": "string"},
                                "ips": {"type": "array", "items": {"type": "string"}},
                                "score": {"type": "number"},
                                "platform": {"type": "string"},
                                "board": {"type": "string"}
                            }
                        }
                    },
                    "kb_data": {
                        "type": "array",
                        "description": "可选: 自定义词库数据, 格式[{标准词, 同义词, 一级分类}]",
                        "items": {"type": "object"}
                    }
                },
                "required": ["items"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "items": {"type": "array"},
                    "total": {"type": "number"},
                    "matched": {"type": "number"},
                    "review": {"type": "number"},
                    "new_values": {"type": "number"}
                }
            }
        }
    ]
}


# ============ HTTP Server ============
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Type', 'application/json')

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            return self.rfile.read(length).decode('utf-8')
        return ''

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/':
            self.send_response(200)
            self._cors()
            self.wfile.write(json.dumps({
                "service": "ip-radar-dedup-a2a",
                "status": "running",
                "kb_size": len(EMBEDDED_KB),
                "timestamp": datetime.datetime.now().isoformat()
            }).encode())
            return

        if path == '/.well-known/agent-card.json' or path == '/agent-card':
            card = dict(AGENT_CARD)
            host = self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or f"localhost:{PORT}"
            proto = self.headers.get('X-Forwarded-Proto') or 'http'
            card['url'] = f"{proto}://{host}"
            self.send_response(200)
            self._cors()
            self.wfile.write(json.dumps(card, ensure_ascii=False, indent=2).encode())
            return

        if path in ('/favicon.ico', '/robots.txt'):
            self.send_response(204)
            self.end_headers()
            return

        self.send_response(404)
        self._cors()
        self.wfile.write(json.dumps({"error": "Not Found"}).encode())

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        if path == '/tasks/send' or path == '/tasks/sendSubscribe':
            try:
                req = json.loads(body)
                req_id = req.get('id')
                params = req.get('params', {})

                # 解析A2A消息
                message = params.get('message', {})
                parts = message.get('parts', [])
                input_data = {}
                for part in parts:
                    if part.get('type') == 'text' and part.get('text'):
                        try:
                            input_data = json.loads(part['text'])
                        except:
                            input_data = {"items": [{"keyword": part['text']}]}
                    elif part.get('type') == 'data' and part.get('data'):
                        try:
                            decoded = base64.b64decode(part['data']).decode('utf-8')
                            input_data = json.loads(decoded)
                        except:
                            pass

                # 也支持直接在params里传参
                if not input_data and 'items' in params:
                    input_data = params

                items = input_data.get('items', [])
                kb_data = input_data.get('kb_data')

                if not items:
                    self._send_error(req_id, -32602, "缺少 items 参数")
                    return

                # 执行去重分类
                result_items = process_dedup(items, kb_data)

                # 统计
                matched = sum(1 for r in result_items if r.get('ip_status') == '匹配成功')
                review = sum(1 for r in result_items if r.get('ip_status') == '需审核')
                new_vals = sum(1 for r in result_items if r.get('ip_status') == '新value')

                result = {
                    "ok": True,
                    "items": result_items,
                    "total": len(result_items),
                    "matched": matched,
                    "review": review,
                    "new_values": new_vals
                }

                if path == '/tasks/sendSubscribe':
                    # SSE流式
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Connection', 'keep-alive')
                    self.end_headers()
                    self.wfile.write(f"event: task\ndata: {json.dumps({'jsonrpc':'2.0','id':req_id,'result':{'id':params.get('id') or f'task-{datetime.datetime.now().timestamp()}','status':{'state':'completed'},'artifacts':[{'name':'dedup-result','parts':[{'type':'text','text':json.dumps(result, ensure_ascii=False)}]}]}})}\n\n".encode())
                else:
                    # 同步响应
                    self.send_response(200)
                    self._cors()
                    self.wfile.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "id": params.get('id') or f"task-{int(datetime.datetime.now().timestamp() * 1000)}",
                            "status": {"state": "completed"},
                            "artifacts": [{
                                "name": "dedup-result",
                                "parts": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                            }]
                        }
                    }).encode())

            except Exception as e:
                self._send_error(None, -32603, f"内部错误: {str(e)}")
            return

        if path == '/dedup':
            # 简化接口: 直接POST JSON
            try:
                data = json.loads(body)
                items = data.get('items', [])
                kb_data = data.get('kb_data')
                result_items = process_dedup(items, kb_data)
                matched = sum(1 for r in result_items if r.get('ip_status') == '匹配成功')
                review = sum(1 for r in result_items if r.get('ip_status') == '需审核')
                new_vals = sum(1 for r in result_items if r.get('ip_status') == '新value')
                self.send_response(200)
                self._cors()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "items": result_items,
                    "total": len(result_items),
                    "matched": matched,
                    "review": review,
                    "new_values": new_vals
                }, ensure_ascii=False).encode())
            except Exception as e:
                self.send_response(500)
                self._cors()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
            return

        self.send_response(404)
        self._cors()
        self.wfile.write(json.dumps({"error": "Not Found"}).encode())

    def _send_error(self, req_id, code, message):
        self.send_response(200)
        self._cors()
        self.wfile.write(json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message}
        }).encode())


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True


if __name__ == '__main__':
    server = ThreadedHTTPServer(('0.0.0.0', PORT), Handler)
    print(f"🚀 IP雷达去重切词 A2A 服务已启动")
    print(f"   端口: {PORT}")
    print(f"   Agent Card: http://localhost:{PORT}/.well-known/agent-card.json")
    print(f"   Tasks/Send: http://localhost:{PORT}/tasks/send")
    print(f"   SSE端点:    http://localhost:{PORT}/tasks/sendSubscribe")
    print(f"   快捷接口:   http://localhost:{PORT}/dedup")
    print(f"   词库规模:   {len(EMBEDDED_KB)} 条")
    server.serve_forever()