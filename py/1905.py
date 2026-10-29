#coding=utf-8
#!/usr/bin/python
# ============================================================================
# 1905电影网 · TVBox Python 爬虫（修复版 2026-10-29）
# ----------------------------------------------------------------------------
# 本次修复点：
#   1. UA：1905 的 WAF 按 UA 放行，PC/iPhone UA 一律 403，改成 Android UA；
#   2. 列表：改为解析 section.search-list / section.search-series 里的
#      a.pic-pack-outer，normal 分类和“系列电影(n_2)”共用一套逻辑；
#   3. 详情：列表链接现在是 /vod/play/{id}.shtml，vod_id 就是纯数字，
#      原代码 split('###')[1] 会直接 IndexError，现支持 纯id / 播放页URL /
#      旧格式 url###title###pic 三种；
#   4. 播放：沿用 profile.m1905.com/mvod/getVideoinfo.php 签名（实测仍可用），
#      但画质优先 hd（uhd 在 m3u8ipay1 付费域），失败时退回“嗅探播放页”；
#   5. 搜索：站内电影搜索页取“立即观看”直链（免费片），VIP 片自动跳过；
#   6. 首页：直接取电影第 1 页，避免 /vod/cctv6/lst/ 结构变化导致空列表。
# ============================================================================
import sys

sys.path.append('..')

try:
    from base.spider import Spider as _BaseSpider
except Exception:                                   # 本机调试时没有 base 包
    _BaseSpider = object

import json
import re
import time
import uuid
import hashlib
import urllib.parse
import urllib.request
import ssl

ssl._create_default_https_context = ssl._create_unverified_context  # 全局取消证书验证


class Spider(_BaseSpider):  # 元类 默认的元类 type

    SITE = 'https://www.1905.com'
    UA = ('Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36')
    TIMEOUT = 15

    def getName(self):
        return '1905电影'

    def init(self, extend=''):
        print('============{0}============'.format(extend))

    def isVideoFormat(self, url):
        u = str(url or '').lower()
        return '.m3u8' in u or '.mp4' in u

    def manualVideoCheck(self):
        return False

    # ------------------------------------------------------------------
    # 分类
    # ------------------------------------------------------------------
    cats = [
        ('电影', 'n_1'),
        ('微电影', 'n_1_c_922'),
        ('系列电影', 'n_2'),
        ('纪录片', 'n_1_c_927'),
        ('晚会', 'n_1_c_586'),
        ('独家', 'n_1_c_178'),
        ('综艺', 'n_1_c_1024'),
    ]

    config = {
        'player': {},
        'filter': {
            'n_1': [{'key': 'by', 'name': '排序:', 'value': [
                {'n': '默认(最热)', 'v': 'o3'},
                {'n': '最新', 'v': 'o1'},
                {'n': '好评', 'v': 'o4'}]}],
            'n_1_c_922': [{'key': 'by', 'name': '排序:', 'value': [
                {'n': '默认(最热)', 'v': 'o3'},
                {'n': '最新', 'v': 'o1'},
                {'n': '好评', 'v': 'o4'}]}],
            'n_2': [{'key': 'by', 'name': '排序:', 'value': [
                {'n': '默认(最热)', 'v': 'o3'},
                {'n': '最新', 'v': 'o1'},
                {'n': '好评', 'v': 'o4'}]}],
            'n_1_c_927': [{'key': 'by', 'name': '排序:', 'value': [
                {'n': '默认(最热)', 'v': 'o3'},
                {'n': '最新', 'v': 'o1'},
                {'n': '好评', 'v': 'o4'}]}],
            'n_1_c_586': [{'key': 'by', 'name': '排序:', 'value': [
                {'n': '默认(最热)', 'v': 'o3'},
                {'n': '最新', 'v': 'o1'},
                {'n': '好评', 'v': 'o4'}]}],
            'n_1_c_178': [{'key': 'by', 'name': '排序:', 'value': [
                {'n': '默认(最热)', 'v': 'o3'},
                {'n': '最新', 'v': 'o1'},
                {'n': '好评', 'v': 'o4'}]}],
            'n_1_c_1024': [{'key': 'by', 'name': '排序:', 'value': [
                {'n': '默认(最热)', 'v': 'o3'},
                {'n': '最新', 'v': 'o1'},
                {'n': '好评', 'v': 'o4'}]}],
        }
    }

    header = {
        # WAF 按 UA 放行：这条 Android UA 实测 200，PC / iPhone UA 会 403
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
                  'image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://www.1905.com/',
    }

    def homeContent(self, filter):
        result = {
            'class': [{'type_name': n, 'type_id': i} for n, i in self.cats],
        }
        if filter:
            result['filters'] = self.config.get('filter', {})
        return result

    def homeVideoContent(self):
        # 直接用电影第 1 页当首页推荐，避免 cctv6/lst 页面结构变动
        try:
            html = self._http(self.SITE + '/vod/list/n_1/o3p1.html',
                              referer=self.SITE + '/vod/')
            return {'list': self._parse_list(html)}
        except Exception:
            return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1

        sort = 'o3'
        ext = extend
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except Exception:
                ext = {}
        if isinstance(ext, dict):
            m = re.search(r'o[1-4]', str(ext.get('by') or ''))
            if m:
                sort = m.group(0)

        tid = str(tid or 'n_1')
        if not re.match(r'^[a-z0-9_]+$', tid):
            tid = 'n_1'

        url = '{0}/vod/list/{1}/{2}p{3}.html'.format(self.SITE, tid, sort, pg)
        try:
            html = self._http(url, referer=self.SITE + '/vod/list/' + tid + '/')
        except Exception:
            html = ''
        videos = self._parse_list(html)
        pagecount = self._page_count(html, sort)

        return {
            'list': videos,
            'page': pg,
            'pagecount': pagecount,
            'limit': len(videos),
            'total': pagecount * (len(videos) or 24),
        }

    # ------------------------------------------------------------------
    # 详情
    # ------------------------------------------------------------------
    def detailContent(self, array):
        raw = array[0] if isinstance(array, (list, tuple)) and array else array
        raw = str(raw or '').strip()
        vid = self._extract_id(raw)
        if not vid:
            return {'list': []}

        info = self._vod_info(vid)
        title = info.get('title') or vid

        items = []
        used = set()
        if info.get('series'):
            for ep in info['series']:
                cid = str(ep.get('contentid') or '').strip()
                name = self._clean(ep.get('title') or '')
                if not cid or cid in used:
                    continue
                used.add(cid)
                items.append('{0}${1}'.format(name or cid, cid))
        if not items:
            items.append('{0}${1}'.format(title, vid))

        vod = {
            'vod_id': raw,
            'vod_name': title,
            'vod_pic': info.get('thumb') or '',
            'type_name': '',
            'vod_year': info.get('year') or '',
            'vod_area': '',
            'vod_remarks': info.get('remark') or '',
            'vod_actor': info.get('starring') or '',
            'vod_director': info.get('direct') or '',
            'vod_content': info.get('description') or '',
            'vod_play_from': '1905线路',
            'vod_play_url': '#'.join(items),
        }
        return {'list': [vod]}

    # ------------------------------------------------------------------
    # 搜索：站内电影搜索，取“立即观看”直链（VIP 片跳过）
    # ------------------------------------------------------------------
    def searchContent(self, key, quick, pg='1'):
        key = str(key or '').strip()
        if not key:
            return {'list': []}
        url = ('https://www.1905.com/search/index-p-type-film-q-{0}.html'
               '?envod=1&year=0&score=0&order=0').format(
            urllib.parse.quote(key, safe=''))
        try:
            html = self._http(url, referer=self.SITE + '/search/')
        except Exception:
            html = ''
        return {'list': self._parse_search(html)}

    # ------------------------------------------------------------------
    # 播放
    # ------------------------------------------------------------------
    def playerContent(self, flag, id, vipFlags):
        page = ''
        pid = self._extract_id(str(id or ''))
        if not pid:
            return {'parse': 1, 'playUrl': '', 'url': '', 'header': self.header}

        if 'vip' in str(flag or '').lower() or 'vip' in str(id or '').lower():
            # VIP 片没有可复现的签名，交给播放器 VIP 解析 / 嗅探
            return {'parse': 1, 'playUrl': '',
                    'url': 'https://vip.1905.com/play/{0}.shtml'.format(pid),
                    'header': self.header}

        page = '{0}/vod/play/{1}.shtml'.format(self.SITE, pid)
        try:
            nonce = int(round(time.time() * 1000))
            expiretime = nonce + 600
            uid = str(uuid.uuid4())
            playerid = uid.replace('-', '')[5:20]
            signature = ('cid={0}&expiretime={1}&nonce={2}&page='
                         'https%3A%2F%2Fwww.1905.com%2Fvod%2Fplay%2F{3}.shtml'
                         '&playerid={4}&type=hls&uuid={5}.dde3d61a0411511d'
                         ).format(pid, expiretime, nonce, pid, playerid, uid)
            signature = hashlib.sha1(signature.encode()).hexdigest()
            url = ('https://profile.m1905.com/mvod/getVideoinfo.php'
                   '?nonce={0}&expiretime={1}&cid={2}&uuid={3}&playerid={4}'
                   '&page=https%3A%2F%2Fwww.1905.com%2Fvod%2Fplay%2F{5}.shtml'
                   '&type=hls&signature={6}&callback=').format(
                nonce, expiretime, pid, uid, playerid, pid, signature)

            jo = self._jsonp(self._http(url, referer=page))
            if str(jo.get('StatusCode')) != '200':
                raise ValueError('bad status')
            data = jo.get('data') or {}
            signs = data.get('sign') or {}
            paths = data.get('path') or {}
            hosts = data.get('quality') or {}

            # 依次尝试 hd -> sd -> uhd：uhd 在 m3u8ipay1 付费域，放最后
            play_url = ''
            for q in ('hd', 'sd', 'uhd'):
                if q not in paths or q not in hosts:
                    continue
                sign = (signs.get(q) or {}).get('sign') or ''
                path = (paths.get(q) or {}).get('path') or ''
                host = (hosts.get(q) or {}).get('host') or ''
                if host and path:
                    play_url = host + sign + path
                    break
            if not play_url:
                raise ValueError('no stream')

            return {'parse': 0, 'playUrl': '', 'url': play_url,
                    'header': {'User-Agent': self.UA, 'Referer': page}}
        except Exception:
            # 签名接口失败时退回“直接播放 + 嗅探”
            return {'parse': 1, 'playUrl': '', 'url': page,
                    'header': self.header}

    # ------------------------------------------------------------------
    # 自定义函数
    # ------------------------------------------------------------------
    def _http(self, url, referer=None, timeout=None):
        h = dict(self.header)
        if referer:
            h['Referer'] = referer
        req = urllib.request.Request(url, headers=h)
        resp = urllib.request.urlopen(req, timeout=timeout or self.TIMEOUT)
        try:
            raw = resp.read()
        finally:
            resp.close()
        return raw.decode('utf-8', 'ignore')

    def _jsonp(self, txt):
        t = str(txt or '').strip()
        a, b = t.find('{'), t.rfind('}')
        if a >= 0 and b > a:
            t = t[a:b + 1]
        return json.loads(t)

    def _clean(self, s):
        s = re.sub(r'<[^>]+>', '', str(s or ''))
        s = s.replace('&nbsp;', ' ').replace('&amp;', '&')
        s = s.replace('&quot;', '"').replace('&#39;', "'")
        return re.sub(r'\s+', ' ', s).strip()

    def _extract_id(self, raw):
        """兼容：纯数字 / 播放页URL / 旧格式 url###title###pic / 1905:123"""
        raw = str(raw or '').strip()
        if not raw:
            return ''
        if raw.startswith('1905:'):
            raw = raw[5:]
        m = re.search(r'/play/(\d+)\.shtml', raw)
        if m:
            return m.group(1)
        m = re.match(r'^(\d+)$', raw)
        if m:
            return m.group(1)
        m = re.match(r'^(\d+)###', raw)
        if m:
            return m.group(1)
        return ''

    def _list_region(self, html):
        """只截取真正的片单区，避开右侧 VIP 推荐和广告位"""
        m = re.search(r'<section[^>]*class="[^"]*search-(?:list|series)[^"]*"',
                      html or '')
        if not m:
            return html or ''
        end = html.find('</section>', m.end())
        return html[m.end():end if end > 0 else len(html)]

    def _parse_list(self, html):
        region = self._list_region(html)
        videos, seen = [], set()
        for m in re.finditer(r'<a\s([^>]*)>', region):
            attrs = dict((k.lower(), v) for k, v in
                         re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', m.group(1)))
            if 'pic-pack-outer' not in attrs.get('class', ''):
                continue
            href = attrs.get('href') or ''
            if 'vip.1905.com' in href:
                continue
            vid = self._extract_id(href)
            if not vid or vid in seen:
                continue
            seen.add(vid)

            end = region.find('</a>', m.end())
            inner = region[m.end():end] if end > 0 else ''

            title = attrs.get('title') or ''
            if not title:
                mm = re.search(r'<img[^>]+alt="([^"]*)"', inner)
                title = mm.group(1) if mm else ''
            if not title:
                mm = re.search(r'<h3[^>]*>([\s\S]*?)</h3>', inner)
                title = self._clean(mm.group(1)) if mm else vid

            mm = re.search(r'<img[^>]+src="([^"]*)"', inner)
            pic = mm.group(1) if mm else ''
            if pic.startswith('//'):
                pic = 'https:' + pic

            remarks = ''
            mm = re.search(r'<i class="score">[\s\S]*?<b>([\d.]+)</b>', inner)
            if mm:
                remarks = '评分 ' + mm.group(1)
            mm = re.search(r'<p[^>]*>([\s\S]*?)</p>', inner)
            if mm and self._clean(mm.group(1)):
                sub = self._clean(mm.group(1))
                remarks = (remarks + ' · ' + sub) if remarks else sub

            videos.append({
                'vod_id': vid,
                'vod_name': self._clean(title),
                'vod_pic': pic,
                'vod_remarks': remarks,
            })
        return videos

    def _page_count(self, html, sort):
        nums = [int(x) for x in
                re.findall(r'/{0}p(\d+)\.html'.format(re.escape(sort)),
                           html or '')]
        return max(nums) if nums else 999

    def _parse_search(self, html):
        videos, seen = [], set()
        blocks = re.split(r'<div class="movie_box\b', html or '')
        if len(blocks) < 2:
            # 皮肤变更兜底：直接扫所有免费播放页链接，取链接前面的海报/片名
            for m in re.finditer(r'href="(https://www\.1905\.com/vod/play/(\d+)'
                                 r'\.shtml)"', html or ''):
                vid = m.group(2)
                if vid in seen:
                    continue
                seen.add(vid)
                ctx = html[max(0, m.start() - 2000):m.start()]
                alt = re.findall(r'<img[^>]+alt="([^"]+)"', ctx)
                src = re.findall(r'<img[^>]+src="([^"]+)"', ctx)
                videos.append({
                    'vod_id': vid,
                    'vod_name': self._clean(alt[-1]) if alt else vid,
                    'vod_pic': src[-1] if src else '',
                    'vod_remarks': '电影',
                })
            return videos

        for block in blocks[1:]:
            m = re.search(r'href="(https?://(?:www\.1905\.com/vod/play|'
                          r'vip\.1905\.com/play)/(\d+)\.shtml)"', block)
            if not m or 'vip.1905.com' in m.group(1):
                continue
            vid = m.group(2)
            if vid in seen:
                continue
            seen.add(vid)

            title = ''
            mm = re.search(r'<img[^>]+alt="([^"]*)"', block)
            if mm:
                title = self._clean(mm.group(1))
            if not title:
                mm = re.search(r'<h2[^>]*>[\s\S]*?<a[^>]+title="([^"]*)"', block)
                title = self._clean(mm.group(1)) if mm else vid

            mm = re.search(r'<img[^>]+src="([^"]*)"', block)
            pic = mm.group(1) if mm else ''
            if pic.startswith('//'):
                pic = 'https:' + pic

            videos.append({
                'vod_id': vid,
                'vod_name': title,
                'vod_pic': pic,
                'vod_remarks': '电影',
            })
        return videos

    def _vod_info(self, vid):
        info = {'title': '', 'thumb': '', 'description': '', 'remark': '',
                'starring': '', 'direct': '', 'year': '', 'series': []}
        page = '{0}/vod/play/{1}.shtml'.format(self.SITE, vid)
        api = ('{0}/api/content/?callback=&m=Vod&a=getVodSidebar'
               '&id={1}&fomat=json').format(self.SITE, vid)

        # 1) 详情接口（字段最全，实测可用）
        try:
            root = self._jsonp(self._http(api, referer=page))
            if isinstance(root, dict) and root.get('title'):
                info['title'] = self._clean(root.get('title'))
                info['thumb'] = root.get('thumb') or ''
                info['description'] = self._clean(root.get('description'))
                info['remark'] = self._clean(
                    root.get('jckd') or root.get('commendreason'))
                info['starring'] = self._clean(root.get('starring'))
                info['direct'] = self._clean(root.get('direct'))
                series = ((root.get('info') or {}).get('series_data')) or []
                info['series'] = [s for s in series
                                  if isinstance(s, dict) and s.get('contentid')]
                return info
        except Exception:
            pass

        # 2) 播放页 VODCONFIG 兜底
        try:
            html = self._http(page, referer=self.SITE + '/vod/')
            m = re.search(r'VODCONFIG\s*=\s*\{([\s\S]*?)\n\s*\}', html)
            body = m.group(1) if m else html
            cfg = dict(re.findall(r'(\w+)\s*:\s*"([^"]*)"', body))
            info['title'] = self._clean(cfg.get('title'))
            info['thumb'] = cfg.get('thumb') or ''
            mm = re.search(r'<meta name="description" content="([^"]*)"', html)
            info['description'] = self._clean(mm.group(1)) if mm else ''
            dur = cfg.get('play_duration') or cfg.get('duration') or ''
            if str(dur).isdigit() and int(dur) > 0:
                info['remark'] = '{0}分钟'.format(max(1, int(dur) // 60))
        except Exception:
            pass
        return info
