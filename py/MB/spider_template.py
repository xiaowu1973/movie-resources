# -*- coding: utf-8 -*-
"""
================================================================================
 TVBox / 影视TV 通用 Python 爬虫模板
================================================================================
 适用：支持 Python 爬虫（base.spider.Spider）的 TVBox 分支，例如影视TV、
       TVBox 改版、影视仓等。
 运行：Python 3.6+（电视盒子内置解释器一般是 3.7 / 3.10，勿用 3.8+ 新语法）
 默认：按苹果 CMS V10 的 JSON 接口实现，改 CONFIG 即可直接用；
       其他站点只需重写 _fetch_home / _fetch_list / _fetch_search / _fetch_detail。

 三分钟上手
 ----------
 1. 复制本文件为 demo_spider.py
 2. 改下面 CONFIG 里的 host / api / classes（把 example.com 换成你的站点）
 3. 本地自测：  python demo_spider.py --self-test
    联网调试：  python demo_spider.py --demo https://站点/api.php/provide/vod/
 4. 按 README.md 写进 TVBox 配置（type=3，api 指向本 py 文件）

 TVBox 会调用的方法
 ------------------
 init(extend)                          初始化，extend 是配置里的 ext
 homeContent(filter)                   首页：分类 + 筛选 + 推荐
 homeVideoContent()                    首页推荐（可选）
 categoryContent(tid, pg, filter, ext) 分类列表 / 翻页
 detailContent(ids)                    详情 + 播放列表
 searchContent(key, quick, pg)         搜索
 playerContent(flag, id, vipFlags)     播放：直链或交给客户端解析
 isVideoFormat(url)                    判断是否视频直链（可选）
================================================================================
"""

import argparse
import json
import re
import sys
import time
from html import unescape as _unescape
from urllib.parse import quote, urlencode, urljoin, urlparse

# ------------------------------------------------------------------------------
# 在 TVBox 里由 base.spider 提供基类；在电脑上直接运行时用下面的本地桩替代，
# 这样同一个文件既能被 TVBox 加载，也能用 python 命令调试。
# ------------------------------------------------------------------------------
try:
    from base.spider import Spider as BaseSpider

    IN_TVBOX = True
except Exception:
    BaseSpider = None
    IN_TVBOX = False


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


# ==============================================================================
# 一、站点配置：绝大多数时候只改这里
# ==============================================================================
CONFIG = {
    # 显示在 TVBox 里的站点名
    "site_name": "模板影视",

    # 站点根地址：协议 + 域名，结尾不要带 /
    "host": "https://www.example.com",

    # 数据接口。默认按苹果 CMS V10：
    #   https://域名/api.php/provide/vod/
    # 如果站点是 HTML 页面，留空，然后重写文件下方的 _fetch_* 方法。
    "api": "https://www.example.com/api.php/provide/vod/",

    # 分类。type_id 必须和接口里的分类 id 一致（苹果 CMS 一般是 1、2、3...）
    "classes": [
        {"type_id": "1", "type_name": "电影"},
        {"type_id": "2", "type_name": "电视剧"},
        {"type_id": "3", "type_name": "综艺"},
        {"type_id": "4", "type_name": "动漫"},
    ],

    # 筛选条件。第一层的键必须等于分类的 type_id；
    # 用户选中的值会以 {key: value} 的形式传进 categoryContent 的 extend。
    # 不需要筛选就整个删掉或留空 {}。
    "filters": {
        "1": [
            {
                "key": "area",
                "name": "地区",
                "value": [
                    {"n": "全部", "v": ""},
                    {"n": "大陆", "v": "大陆"},
                    {"n": "香港", "v": "香港"},
                    {"n": "美国", "v": "美国"},
                ],
            },
            {
                "key": "year",
                "name": "年份",
                "value": [
                    {"n": "全部", "v": ""},
                    {"n": "2025", "v": "2025"},
                    {"n": "2024", "v": "2024"},
                    {"n": "2023", "v": "2023"},
                ],
            },
        ],
        "2": [
            {
                "key": "area",
                "name": "地区",
                "value": [
                    {"n": "全部", "v": ""},
                    {"n": "大陆", "v": "大陆"},
                    {"n": "韩国", "v": "韩国"},
                ],
            },
        ],
    },

    # 普通请求头。很多站点校验 UA，按需加 Referer / Cookie
    "headers": {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    },

    # 播放请求头：m3u8 / mp4 有防盗链时，Referer 往往必须填
    "play_headers": {
        "User-Agent": DEFAULT_UA,
        # "Referer": "https://www.example.com/",
    },

    # 解析接口。留空 = 交给客户端自带的解析器（推荐）；
    # 填了之后，非直链地址会带上 {"parse":1,"playUrl":解析接口,"url":原地址}。
    # 支持占位符 {url}，例如 "https://jx.example.com/?url={url}"。
    "parse_api": "",

    # 网络参数
    "timeout": 10,
    "retry": 1,

    # 苹果 CMS 的分页/分类/搜索参数名，个别站点不同时可改
    "page_key": "pg",
    "tid_key": "t",
    "search_key": "wd",
}


# 离线自检用的假数据（模拟苹果 CMS 接口返回），正式使用时可忽略
SAMPLE_LIST = {
    "code": 1,
    "msg": "数据列表",
    "page": 1,
    "pagecount": 3,
    "limit": "20",
    "total": "58",
    "list": [
        {
            "vod_id": 1001,
            "vod_name": "示例电影",
            "vod_pic": "/upload/1.jpg",
            "vod_remarks": "HD国语",
        },
        {
            "vod_id": 1002,
            "vod_name": "示例剧集",
            "vod_pic": "https://img.example.com/2.jpg",
            "vod_remarks": "更新至12集",
        },
    ],
}

SAMPLE_DETAIL = {
    "code": 1,
    "list": [
        {
            "vod_id": 1001,
            "vod_name": "示例电影",
            "vod_pic": "/upload/1.jpg",
            "type_name": "动作片",
            "vod_year": "2025",
            "vod_area": "大陆",
            "vod_actor": "张三,李四",
            "vod_director": "王五",
            "vod_content": "<p>这是一段&nbsp;剧情简介。</p>",
            "vod_play_from": "m3u8$$$mp4",
            "vod_play_url": (
                "第01集$/play/1-1-1.m3u8#第02集$/play/1-1-2.m3u8"
                "$$$正片$/play/1-2-1.mp4"
            ),
        }
    ],
}


# ==============================================================================
# 二、本地调试桩：只在电脑上 python 直接运行时生效
# ==============================================================================
class _LocalResponse(object):
    def __init__(self, content, url, status_code=200, headers=None):
        self.content = content or b""
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        charset = "utf-8"
        try:
            content_type = self.headers.get("Content-Type", "") or ""
        except Exception:
            content_type = ""
        match = re.search(r"charset=([\w-]+)", content_type, re.I)
        if match:
            charset = match.group(1)
        self.encoding = charset
        try:
            self.text = self.content.decode(charset)
        except Exception:
            self.text = self.content.decode("gb18030", "ignore")

    def json(self):
        return json.loads(self.text)


class _LocalSpider(object):
    """模拟 base.spider.Spider 的 fetch / post，方便电脑上跑 --demo。"""

    def _open(self, url, params=None, headers=None, timeout=None, data=None):
        import urllib.request

        if params:
            query = urlencode({k: v for k, v in params.items() if v is not None})
            url = url + ("&" if "?" in url else "?") + query
        body = None
        if data is not None:
            if isinstance(data, dict):
                body = urlencode(data).encode("utf-8")
            elif isinstance(data, str):
                body = data.encode("utf-8")
            else:
                body = data
        request = urllib.request.Request(url, data=body, headers=headers or {})
        with urllib.request.urlopen(request, timeout=timeout or 10) as response:
            return _LocalResponse(
                response.read(),
                response.geturl(),
                getattr(response, "status", 200),
                dict(response.headers),
            )

    def fetch(self, url, params=None, headers=None, timeout=None, **kwargs):
        return self._open(url, params=params, headers=headers, timeout=timeout)

    def post(self, url, params=None, data=None, headers=None, timeout=None, **kwargs):
        return self._open(
            url, params=params, headers=headers, timeout=timeout, data=data
        )


if not IN_TVBOX:
    BaseSpider = _LocalSpider


# ==============================================================================
# 三、爬虫主体
# ==============================================================================
class Spider(BaseSpider):
    """TVBox 爬虫入口。类名必须叫 Spider，方法名必须和下面一致。"""

    def __init__(self):
        try:
            super(Spider, self).__init__()
        except Exception:
            pass
        self.cfg = {}
        self.site_name = "TVBox Spider"
        self.host = ""
        self.api = ""
        self.headers = {}
        self.play_headers = {}
        self.timeout = 10
        self.retry = 1
        self.page_key = "pg"
        self.tid_key = "t"
        self.search_key = "wd"
        self.classes = []
        self.filters = {}
        self._apply_config(CONFIG)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def init(self, extend=""):
        """TVBox 初始化时调用；extend 就是配置里的 ext 字段（原样传入）。"""
        ext = self._parse_extend(extend)
        cfg = json.loads(json.dumps(CONFIG, ensure_ascii=False))
        self._deep_update(cfg, ext)

        # 允许 ext 里写 {"config_url": "https://.../config.json"} 做远程配置
        remote = cfg.pop("config_url", "")
        if remote:
            data = self._get_json(str(remote))
            if isinstance(data, dict) and data:
                self._deep_update(cfg, data)

        self._apply_config(cfg)
        return self

    def getName(self):
        return self.site_name

    def isVideoFormat(self, url):
        return self._is_media_url(str(url or ""))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        # 如果自己建了 Session / 定时任务，在这里释放；模板用不到
        pass

    # ------------------------------------------------------------------
    # 首页
    # ------------------------------------------------------------------
    def homeContent(self, filter=False):
        classes = [dict(item) for item in self.classes]
        filters = self._copy(self.filters) if filter else {}
        videos = []
        try:
            rows = self._fetch_home()
            videos = [self._video_item(row) for row in rows]
        except Exception as exc:
            self._log("homeContent 失败: {}".format(exc))
        return {"class": classes, "filters": filters, "list": videos}

    def homeVideoContent(self):
        try:
            rows = self._fetch_home()
            return {"list": [self._video_item(row) for row in rows]}
        except Exception as exc:
            self._log("homeVideoContent 失败: {}".format(exc))
            return {"list": []}

    # ------------------------------------------------------------------
    # 分类 / 翻页
    # ------------------------------------------------------------------
    def categoryContent(self, tid, pg, filter, extend):
        page = self._to_int(pg, 1)
        if page < 1:
            page = 1
        tid = "" if tid is None else str(tid)
        extend = extend if isinstance(extend, dict) else {}

        result = {"page": page, "pagecount": page, "limit": 0, "total": 0, "list": []}
        try:
            data = self._fetch_list(tid, page, extend)
            rows = data.get("list") or []
            limit = self._to_int(data.get("limit"), 0) or len(rows) or 20
            total = self._to_int(data.get("total"), 0)
            pagecount = self._to_int(data.get("pagecount"), 0)
            if pagecount <= 0:
                if total > 0:
                    pagecount = (total + limit - 1) // limit
                else:
                    # 总数未知：本页满页就认为还有下一页
                    pagecount = page + 1 if len(rows) >= limit else page
            result.update(
                {
                    "page": page,
                    "pagecount": max(pagecount, page),
                    "limit": limit,
                    "total": total,
                    "list": [self._video_item(row) for row in rows],
                }
            )
        except Exception as exc:
            self._log("categoryContent 失败: {}".format(exc))
        return result

    # ------------------------------------------------------------------
    # 详情
    # ------------------------------------------------------------------
    def detailContent(self, ids):
        if isinstance(ids, (str, int)):
            ids = [ids]
        items = []
        for vod_id in ids or []:
            vod_id = str(vod_id)
            if not vod_id:
                continue
            try:
                row = self._fetch_detail(vod_id)
                if row:
                    items.append(self._detail_item(row))
            except Exception as exc:
                self._log("detailContent({}) 失败: {}".format(vod_id, exc))
        return {"list": items}

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def searchContent(self, key, quick=False, pg="1"):
        keyword = str(key or "").strip()
        if not keyword:
            return {"list": []}
        try:
            rows = self._fetch_search(keyword)
            return {"list": [self._video_item(row) for row in rows]}
        except Exception as exc:
            self._log("searchContent 失败: {}".format(exc))
            return {"list": []}

    # ------------------------------------------------------------------
    # 播放
    # ------------------------------------------------------------------
    def playerContent(self, flag, id, vipFlags=None):
        url = str(id or "").strip()
        header = dict(self.play_headers)
        if not url:
            return {"parse": 0, "playUrl": "", "url": "", "header": header}

        # 1) 本身是 m3u8 / mp4 等直链：直接播
        if self.isVideoFormat(url):
            return {"parse": 0, "playUrl": "", "url": url, "header": header}

        # 2) 配置了解析接口：交给它解析（老版 TVBox 的 playUrl 用法）
        parse_api = str(self.cfg.get("parse_api") or "").strip()
        if parse_api:
            play_url = parse_api.replace("{url}", quote(url, safe=""))
            return {
                "parse": 1,
                "playUrl": play_url,
                "url": url,
                "header": header,
            }

        # 3) 默认：parse=1、playUrl 留空，让客户端用自带的解析器处理
        return {"parse": 1, "playUrl": "", "url": url, "header": header}

    # ==================================================================
    # 四、站点适配区
    # ==================================================================
    # 默认实现针对苹果 CMS V10 的 JSON 接口。换站点时：
    #   JSON 接口  -> 只改 CONFIG 的 host / api / classes / filters
    #   HTML 站点  -> 重写 _fetch_list、_fetch_search、_fetch_detail 三个方法，
    #                 返回统一字段即可（见 README 的“HTML 站点改造示例”）
    # ------------------------------------------------------------------

    def _fetch_home(self):
        """首页推荐。默认取第一个分类的第一页，不需要可以 return []。"""
        tid = ""
        if self.classes:
            tid = str(self.classes[0].get("type_id") or "")
        data = self._fetch_list(tid, 1, {})
        return data.get("list") or []

    def _fetch_list(self, tid, pg, extend):
        """分类列表，返回 {"list": [...], "total": 0, "pagecount": 0, "limit": 0}。"""
        result = {"list": [], "total": 0, "pagecount": 0, "limit": 0}
        if not self.api:
            return result

        params = {"ac": "list"}
        if tid:
            params[self.tid_key] = tid
        params[self.page_key] = pg
        for key, value in (extend or {}).items():
            if value is None or value == "":
                continue
            params[key] = value

        data = self._get_json(self.api, params)
        result["list"] = data.get("list") or []
        result["total"] = self._to_int(data.get("total"), 0)
        result["pagecount"] = self._to_int(data.get("pagecount"), 0)
        result["limit"] = self._to_int(data.get("limit"), 0)
        return result

    def _fetch_search(self, key):
        """搜索，返回原始行列表。"""
        if not self.api:
            return []
        data = self._get_json(self.api, {self.search_key: key})
        rows = data.get("list") or []
        if not rows:
            # 有些苹果 CMS 站点要求 ac=detail 才返回搜索结果
            data = self._get_json(self.api, {"ac": "detail", self.search_key: key})
            rows = data.get("list") or []
        return rows

    def _fetch_detail(self, vod_id):
        """详情，返回单行 dict；找不到返回 None。"""
        if not self.api:
            return None
        data = self._get_json(self.api, {"ac": "detail", "ids": vod_id})
        rows = data.get("list") or []
        if not rows:
            # 少数站点详情参数用 id 而不是 ids
            data = self._get_json(self.api, {"ac": "detail", "id": vod_id})
            rows = data.get("list") or []
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # 苹果 CMS 行数据 -> TVBox 标准字段
    # ------------------------------------------------------------------
    def _video_item(self, row):
        row = row if isinstance(row, dict) else {}
        return {
            "vod_id": str(row.get("vod_id") or row.get("id") or ""),
            "vod_name": self._clean(row.get("vod_name") or row.get("name") or ""),
            "vod_pic": self._abs_url(
                row.get("vod_pic") or row.get("pic") or row.get("vod_pic_thumb") or ""
            ),
            "vod_remarks": self._clean(
                row.get("vod_remarks") or row.get("note") or row.get("remarks") or ""
            ),
        }

    def _detail_item(self, row):
        row = row if isinstance(row, dict) else {}
        item = self._video_item(row)
        play_from, play_url = self._normalize_play(
            row.get("vod_play_from"), row.get("vod_play_url")
        )
        item.update(
            {
                "type_name": self._clean(row.get("type_name") or row.get("vod_class") or ""),
                "vod_year": self._clean(row.get("vod_year") or ""),
                "vod_area": self._clean(row.get("vod_area") or ""),
                "vod_lang": self._clean(row.get("vod_lang") or ""),
                "vod_actor": self._clean(row.get("vod_actor") or ""),
                "vod_director": self._clean(row.get("vod_director") or ""),
                "vod_content": self._clean(
                    row.get("vod_content") or row.get("vod_blurb") or ""
                ),
                "vod_play_from": play_from,
                "vod_play_url": play_url,
            }
        )
        return item

    # ==================================================================
    # 五、内部工具：一般不用改
    # ==================================================================

    # ---------------- 配置 ----------------
    @staticmethod
    def _parse_extend(extend):
        if extend is None:
            return {}
        if isinstance(extend, dict):
            return dict(extend)
        text = str(extend).strip()
        if not text:
            return {}
        if text[0] in "{[":
            try:
                data = json.loads(text)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        if text.startswith("http"):
            return {"api": text}
        return {}

    @staticmethod
    def _deep_update(base, extra):
        for key, value in (extra or {}).items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                Spider._deep_update(base[key], value)
            else:
                base[key] = value
        return base

    @staticmethod
    def _copy(obj):
        try:
            return json.loads(json.dumps(obj, ensure_ascii=False))
        except Exception:
            return obj

    def _apply_config(self, cfg):
        cfg = cfg if isinstance(cfg, dict) else {}
        self.cfg = cfg
        self.site_name = str(cfg.get("site_name") or "TVBox Spider")
        self.host = str(cfg.get("host") or "").strip().rstrip("/")

        api = str(cfg.get("api") or "").strip()
        if api and not re.match(r"^https?://", api, re.I):
            api = urljoin(self.host + "/", api)
        self.api = api

        self.headers = dict(cfg.get("headers") or {})
        self.play_headers = dict(cfg.get("play_headers") or {})
        if not self.play_headers:
            self.play_headers = dict(self.headers)

        self.timeout = self._to_int(cfg.get("timeout"), 10) or 10
        self.retry = self._to_int(cfg.get("retry"), 1)
        if self.retry < 0:
            self.retry = 0

        self.page_key = str(cfg.get("page_key") or "pg")
        self.tid_key = str(cfg.get("tid_key") or "t")
        self.search_key = str(cfg.get("search_key") or "wd")

        classes = cfg.get("classes") or []
        self.classes = [dict(item) for item in classes if isinstance(item, dict)]
        filters = cfg.get("filters") or {}
        self.filters = filters if isinstance(filters, dict) else {}

    # ---------------- HTTP ----------------
    def _request(self, url, params=None, headers=None, data=None, timeout=None):
        """统一请求入口：优先兼容新版运行时的 self.net，其次经典 self.fetch。"""
        hdrs = dict(self.headers)
        if headers:
            hdrs.update(headers)
        wait = timeout or self.timeout

        net = getattr(self, "net", None)
        if net is not None and hasattr(net, "req"):
            return self._request_net(net, url, params, hdrs, data, wait)
        return self._request_fetch(url, params, hdrs, data, wait)

    def _request_net(self, net, url, params, headers, data, wait):
        options = {"headers": headers, "timeout": int(wait * 1000)}
        if params:
            options["params"] = {
                str(k): str(v) for k, v in params.items() if v is not None
            }
        if data is not None:
            options["method"] = "POST"
            options["data"] = data
        try:
            result = net.req(url, options)
        except Exception as exc:
            self._log("请求失败: {} ({})".format(url, exc))
            return None
        if not isinstance(result, dict):
            return None
        if result.get("error"):
            self._log("请求失败: {} ({})".format(url, result.get("error")))
            return None
        code = self._to_int(result.get("code"), 0)
        if code and not 200 <= code < 300:
            self._log("HTTP {}: {}".format(code, url))
            return None
        return _NetResponse(result)

    def _request_fetch(self, url, params, headers, data, wait):
        retry = self.retry if self.retry > 0 else 0
        error = None
        for attempt in range(retry + 1):
            try:
                if data is None:
                    resp = self.fetch(
                        url, params=params, headers=headers, timeout=wait
                    )
                else:
                    resp = self.post(
                        url, params=params, data=data, headers=headers, timeout=wait
                    )
                if resp is not None:
                    return resp
            except TypeError:
                # 个别旧运行环境的 fetch 不支持 params / timeout，手动拼参数再试
                try:
                    fallback_url = url
                    if params:
                        query = urlencode(
                            {k: v for k, v in params.items() if v is not None}
                        )
                        fallback_url += ("&" if "?" in url else "?") + query
                    if data is None:
                        resp = self.fetch(fallback_url, headers=headers, timeout=wait)
                    else:
                        resp = self.post(
                            fallback_url, data=data, headers=headers, timeout=wait
                        )
                    if resp is not None:
                        return resp
                except Exception as exc:
                    error = exc
            except Exception as exc:
                error = exc
            if attempt < retry:
                time.sleep(0.3)
        if error is not None:
            self._log("请求失败: {} ({})".format(url, error))
        return None

    def _get_text(self, url, params=None, headers=None):
        resp = self._request(url, params=params, headers=headers)
        if resp is None:
            return ""
        return self._resp_text(resp)

    def _get_json(self, url, params=None, headers=None):
        text = self._get_text(url, params=params, headers=headers)
        text = text.strip().lstrip("\ufeff")
        if not text:
            return {}
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            self._log("JSON 解析失败: {}...".format(text[:120]))
            return {}

    @staticmethod
    def _resp_text(resp):
        """取响应文本：处理 requests 默认 ISO-8859-1 导致的中文乱码。"""
        try:
            content = resp.content or b""
        except Exception:
            content = b""

        encoding = ""
        try:
            encoding = (resp.encoding or "").lower()
        except Exception:
            encoding = ""

        if content:
            if encoding in ("", "iso-8859-1", "latin-1", "latin1", "ascii"):
                # 没有 charset 或默认 Latin-1 时，按中文站点最常见的编码试
                for candidate in ("utf-8-sig", "utf-8", "gb18030"):
                    try:
                        return content.decode(candidate)
                    except Exception:
                        continue
            else:
                try:
                    return content.decode(encoding)
                except Exception:
                    pass

        text = ""
        try:
            text = resp.text or ""
        except Exception:
            text = ""
        if not text and content:
            text = content.decode("utf-8", "ignore")
        return text

    # ---------------- 播放列表 ----------------
    def _normalize_play(self, froms, urls):
        """
        把接口返回的播放信息整理成 TVBox 格式：
          多条线路用 $$$ 分隔，每集的“名称$地址”用 # 分隔。
        """
        urls_text = str(urls or "").strip()
        if not urls_text:
            return "", ""

        from_list = [
            item.strip() for item in str(froms or "").split("$$$") if item.strip()
        ]
        groups = []
        for raw in urls_text.split("$$$"):
            episodes = []
            for name, link in self._split_episodes(raw):
                link = self._abs_url(link)
                if not link:
                    continue
                if not name:
                    name = "第{}集".format(len(episodes) + 1)
                episodes.append("{}${}".format(name, link))
            if episodes:
                groups.append("#".join(episodes))

        while len(from_list) < len(groups):
            from_list.append("线路{}".format(len(from_list) + 1))
        return "$$$".join(from_list[: len(groups)]), "$$$".join(groups)

    @staticmethod
    def _split_episodes(text):
        items = []
        for raw in str(text or "").split("#"):
            raw = raw.strip()
            if not raw:
                continue
            if "$" in raw:
                name, link = raw.split("$", 1)
            else:
                name, link = "", raw
            items.append((name.strip(), link.strip()))
        return items

    @staticmethod
    def _is_media_url(url):
        return bool(
            re.search(
                r"\.(m3u8|mp4|flv|mkv|avi|mov|ts|m4v|webm|mpd)(\?|#|$)", url, re.I
            )
        )

    # ---------------- 文本 / URL ----------------
    def _abs_url(self, url):
        text = str(url or "").strip()
        if not text:
            return ""
        if text.startswith("//"):
            return "https:" + text
        if text.startswith("data:"):
            return text
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
            return text
        if not self.host:
            return text
        return urljoin(self.host + "/", text)

    @staticmethod
    def _clean(text):
        if text is None:
            return ""
        value = str(text)
        value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
        value = re.sub(r"<[^>]+>", " ", value)
        value = _unescape(value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    @staticmethod
    def _to_int(value, default=0):
        try:
            return int(float(str(value).strip()))
        except Exception:
            return default

    def _log(self, message):
        text = "[{}] {}".format(self.site_name, message)
        try:
            logger = getattr(self, "log", None)
            if callable(logger):
                logger(text)
                return
        except Exception:
            pass
        try:
            print(text)
        except Exception:
            pass


class _NetResponse(object):
    """把新版运行时 self.net.req 的结果包装成 requests.Response 的样子。"""

    def __init__(self, payload):
        self.status_code = int(payload.get("code") or 0)
        self.url = payload.get("url") or ""
        self.headers = payload.get("headers") or {}
        content = payload.get("content")
        if isinstance(content, bytes):
            self.content = content
            try:
                self.text = content.decode("utf-8")
            except Exception:
                self.text = content.decode("gb18030", "ignore")
        elif isinstance(content, str):
            self.content = content.encode("utf-8")
            self.text = content
        else:
            self.content = b""
            self.text = ""

    def json(self):
        return json.loads(self.text)


# ==============================================================================
# 六、本地调试入口（TVBox 加载时不会执行）
# ==============================================================================
def _self_test():
    print("=" * 62)
    print("TVBox Python 爬虫模板 - 离线自检（不联网）")
    print("=" * 62)
    failures = []

    def check(name, ok, detail=""):
        print("[{}] {}{}".format("通过" if ok else "失败", name, "" if ok else "  -> " + str(detail)))
        if not ok:
            failures.append(name)

    spider = Spider()
    spider.init("{}")
    spider.api = ""  # 自检不联网
    spider.host = "https://www.example.com"  # 固定测试域名，避免受 CONFIG 修改影响

    home = spider.homeContent(True)
    check("homeContent 三个键", set(["class", "filters", "list"]).issubset(home.keys()), home.keys())
    check("分类数量", len(home["class"]) >= 1, home["class"])
    check("筛选条件", isinstance(home["filters"], dict) and len(home["filters"]) >= 1)

    item = spider._video_item(SAMPLE_LIST["list"][0])
    check("列表 vod_id", item["vod_id"] == "1001", item)
    check("列表 vod_name", item["vod_name"] == "示例电影", item)
    check("相对海报补全", item["vod_pic"] == "https://www.example.com/upload/1.jpg", item["vod_pic"])

    detail = spider._detail_item(SAMPLE_DETAIL["list"][0])
    check("播放线路数量", detail["vod_play_from"] == "m3u8$$$mp4", detail["vod_play_from"])
    check("播放集数", detail["vod_play_url"].count("$") == 3, detail["vod_play_url"])
    check(
        "播放地址补全",
        "https://www.example.com/play/1-1-1.m3u8" in detail["vod_play_url"],
        detail["vod_play_url"],
    )
    check("简介去标签", "剧情简介" in detail["vod_content"], detail["vod_content"])

    play = spider.playerContent("m3u8", "https://cdn.example.com/a/index.m3u8", None)
    check("m3u8 直链 parse=0", play["parse"] == 0, play)

    play = spider.playerContent("web", "https://www.example.com/play/1001.html", None)
    check("网页地址 parse=1", play["parse"] == 1, play)

    spider.cfg["parse_api"] = "https://jx.example.com/?url={url}"
    play = spider.playerContent("web", "https://www.example.com/play/1001.html", None)
    check("解析接口拼接", play["parse"] == 1 and "jx.example.com" in play["playUrl"], play)

    froms, urls = spider._normalize_play("", "第1集$/a.m3u8#第2集$/b.m3u8")
    check("缺省线路名", froms == "线路1", froms)
    check("单线路地址", urls == "第1集$https://www.example.com/a.m3u8#第2集$https://www.example.com/b.m3u8", urls)

    check("HTML 清洗", spider._clean("<p>你好&nbsp;世界</p>") == "你好 世界", spider._clean("<p>你好&nbsp;世界</p>"))

    print("-" * 62)
    if failures:
        print("未通过 {} 项：{}".format(len(failures), "、".join(failures)))
        return False
    print("全部通过。接下来可以改 CONFIG，再用 --demo 联网调试。")
    return True


def _demo(api_url, keyword):
    api_url = (api_url or "").strip()
    if not api_url:
        print("用法: python {} --demo https://站点/api.php/provide/vod/".format(sys.argv[0]))
        return 1

    parsed = urlparse(api_url)
    ext = {"api": api_url}
    if parsed.netloc:
        ext["host"] = "{}://{}".format(parsed.scheme or "https", parsed.netloc)

    spider = Spider()
    spider.init(json.dumps(ext, ensure_ascii=False))

    print("接口:", spider.api)
    print("站点:", spider.host)

    home = spider.homeContent(True)
    print("\n[首页] 分类 {} 个，推荐 {} 条".format(len(home["class"]), len(home["list"])))
    for item in home["class"]:
        print("  {}  {}".format(item.get("type_id"), item.get("type_name")))
    for video in home["list"][:5]:
        print("  - {} {} {}".format(video["vod_id"], video["vod_name"], video["vod_remarks"]))

    tid = str(home["class"][0].get("type_id")) if home["class"] else ""
    category = spider.categoryContent(tid, 1, True, {})
    print(
        "\n[分类 {}] 第 1 页 {} 条，pagecount={}".format(
            tid, len(category["list"]), category["pagecount"]
        )
    )
    for video in category["list"][:5]:
        print("  - {} {} {}".format(video["vod_id"], video["vod_name"], video["vod_remarks"]))

    search = spider.searchContent(keyword, False)
    print("\n[搜索 {}] {} 条".format(keyword, len(search["list"])))
    for video in search["list"][:5]:
        print("  - {} {} {}".format(video["vod_id"], video["vod_name"], video["vod_remarks"]))

    if category["list"]:
        vod_id = category["list"][0]["vod_id"]
        detail = spider.detailContent([vod_id])
        if detail["list"]:
            vod = detail["list"][0]
            print("\n[详情 {}] {}".format(vod_id, vod.get("vod_name")))
            print("  线路:", vod.get("vod_play_from"))
            first = (vod.get("vod_play_url") or "").split("$$$")[0].split("#")[0]
            print("  第一集:", first)
            play_id = first.split("$", 1)[1] if "$" in first else first
            play = spider.playerContent("", play_id, None)
            print("  播放返回:", json.dumps(play, ensure_ascii=False)[:300])
        else:
            print("\n[详情 {}] 没有取到数据".format(vod_id))

    return 0


def main():
    parser = argparse.ArgumentParser(description="TVBox Python 爬虫模板（自带离线自检）")
    parser.add_argument("--self-test", action="store_true", help="离线自检，不联网")
    parser.add_argument("--demo", metavar="API", default="", help="苹果 CMS 接口地址，联网跑完整流程")
    parser.add_argument("--keyword", default="庆余年", help="--demo 时使用的搜索关键词")
    args = parser.parse_args()

    if args.self_test:
        return 0 if _self_test() else 1
    if args.demo:
        return _demo(args.demo, args.keyword)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
