#!/usr/bin/env python3

import asyncio
import time
from datetime import timedelta

from html.parser import HTMLParser
from urllib.parse import urljoin, urldefrag

from tornado import gen, httpclient, queues

base_url = "http://www.tornadoweb.org/en/stable/"
concurrency = 10


async def get_links_from_url(url):
    """Download the page at `url` and parse it for links.

    Returned links have had the fragment after `#` removed, and have been made
    absolute so, e.g. the URL 'gen.html#tornado.gen.coroutine' becomes
    'http://www.tornadoweb.org/en/stable/gen.html'.
    """
    response = await httpclient.AsyncHTTPClient().fetch(url)
    print("fetched %s" % url)

    html = response.body.decode(errors="ignore")
    return [urljoin(url, remove_fragment(new_url)) for new_url in get_links(html)]


def remove_fragment(url):
    pure_url, frag = urldefrag(url)
    return pure_url


def get_links(html):
    class URLSeeker(HTMLParser):
        def __init__(self):
            HTMLParser.__init__(self)
            self.urls = []

        def handle_starttag(self, tag, attrs):
            href = dict(attrs).get("href")
            if href and tag == "a":
                self.urls.append(href)

    url_seeker = URLSeeker()
    url_seeker.feed(html)
    return url_seeker.urls


async def main():
    # 创建 url 队列
    # 工作区、完成区、出错区
    q = queues.Queue()
    start = time.time()
    fetching, fetched, dead = set(), set(), set()

    async def fetch_url(current_url):
        # 忽略正在爬取（工作区）的 url
        if current_url in fetching:
            return

        print("fetching %s" % current_url)
        fetching.add(current_url)
        urls = await get_links_from_url(current_url)
        # 爬取完成放进完成区
        fetched.add(current_url)

        # 筛选新的，并加入到队列
        for new_url in urls:
            # Only follow links beneath the base URL
            if new_url.startswith(base_url):
                await q.put(new_url)

    async def worker():
        async for url in q:
            if url is None:
                return
            try:
                await fetch_url(url)
            except Exception as e:
                print("Exception: %s %s" % (e, url))
                dead.add(url)
            finally:
                # 完成后调用 task_done，队列中的未完成任务计数将减 1， 与 q.get 一一对应。
                # 为的是配合 q.join， 当计数为 0 时， q.join 将结束等待。
                q.task_done()

    # 为队列添加第一个 url （任务）
    await q.put(base_url)

    # Start workers, then wait for the work queue to be empty.
    workers = gen.multi([worker() for _ in range(concurrency)])
    # 使用 join 阻塞等待，直到队列被清空才会恢复，继续往下走
    await q.join(timeout=timedelta(seconds=300))
    assert fetching == (fetched | dead)
    print("Done in %d seconds, fetched %s URLs." % (time.time() - start, len(fetched)))
    print("Unable to fetch %s URLs." % len(dead))

    # Signal all the workers to exit.
    for _ in range(concurrency):
        await q.put(None)
    await workers


if __name__ == "__main__":
    asyncio.run(main())
