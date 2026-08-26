from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import defusedxml.ElementTree as ET

from fedifetcher.state import ServerCache

if TYPE_CHECKING:
    from fedifetcher.http import HttpClient

logger = logging.getLogger("FediFetcher")

NODEINFO_SCHEMAS = (
    'http://nodeinfo.diaspora.software/ns/schema/2.0',
    'http://nodeinfo.diaspora.software/ns/schema/2.1',
)


class ApiFlavour(StrEnum):
    MASTODON = "mastodon"
    MISSKEY = "misskey"
    LEMMY = "lemmy"
    PEERTUBE = "peertube"


# support for new server software should be added here
SOFTWARE_APIS: dict[ApiFlavour, frozenset[str]] = {
    ApiFlavour.MASTODON: frozenset({
        'mastodon', 'pleroma', 'akkoma', 'pixelfed', 'hometown', 'iceshrimp',
        'Iceshrimp.NET', 'fedibird', 'kmyblue', 'mitra',
    }),
    ApiFlavour.MISSKEY: frozenset({
        'misskey', 'calckey', 'firefish', 'foundkey', 'sharkey', 'cherrypick',
    }),
    ApiFlavour.LEMMY: frozenset({'lemmy'}),
    ApiFlavour.PEERTUBE: frozenset({'peertube'}),
}

# software that has specific API support but is not compatible with FediFetcher for various reasons:
# * gotosocial - All Mastodon APIs require access token (https://github.com/superseriousbusiness/gotosocial/issues/2038)

# how each flavour is spelled in the seen_hosts file
_STATE_KEYS = {flavour: f"{flavour}ApiSupport" for flavour in ApiFlavour}


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """What we know about a server, and which APIs we can talk to it with"""

    webserver: str
    software: str
    version: str
    apis: frozenset[ApiFlavour]
    last_checked: datetime
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def supports(self, flavour: ApiFlavour) -> bool:
        return flavour in self.apis

    @classmethod
    def from_nodeinfo(cls, webserver: str, nodeinfo: dict[str, Any]) -> ServerInfo:
        software = nodeinfo['software']['name']
        apis = {
            flavour
            for flavour, names in SOFTWARE_APIS.items()
            if software in names
        }

        # search `features` list in metadata if available
        features = nodeinfo.get('metadata', {}).get('features')
        if isinstance(features, list) and 'mastodon_api' in features:
            apis.add(ApiFlavour.MASTODON)

        return cls(
            webserver=webserver,
            software=software,
            version=nodeinfo['software']['version'],
            apis=frozenset(apis),
            last_checked=datetime.now(),
            raw=nodeinfo,
        )

    def to_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            'webserver': self.webserver,
            'software': self.software,
            'version': self.version,
            'rawnodeinfo': self.raw,
            'last_checked': self.last_checked,
        }
        state.update({key: flavour in self.apis for flavour, key in _STATE_KEYS.items()})
        return state

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> ServerInfo | None:
        """Read a seen_hosts entry, or None if it records a failed lookup"""
        if state.get('info', False) is None or 'webserver' not in state:
            return None
        return cls(
            webserver=state['webserver'],
            software=state['software'],
            version=state['version'],
            apis=frozenset(
                flavour for flavour, key in _STATE_KEYS.items() if state.get(key)
            ),
            last_checked=state['last_checked'],
            raw=state.get('rawnodeinfo', {}),
        )


def failed_lookup_state() -> dict[str, Any]:
    return {'info': None, 'last_checked': datetime.now()}


def get_server_info(
    server: str, cache: ServerCache, *, http: HttpClient
) -> ServerInfo | None:
    """Look a server up, remembering both successes and failures"""
    if server in cache:
        return ServerInfo.from_state(cache.get(server))

    info = get_nodeinfo(server, cache, http=http)
    if info is None:
        cache.add(server, failed_lookup_state())
    else:
        cache.add(server, info.to_state())
        if server != info.webserver:
            cache.add(info.webserver, info.to_state())
    return info


def get_nodeinfo(
    server: str, cache: ServerCache, host_meta_fallback: bool = False, *,
    http: HttpClient,
) -> ServerInfo | None:
    url = f'https://{server}/.well-known/nodeinfo'
    try:
        resp = http.get(url, timeout = 30)
    except Exception as ex:
        logger.error(f"Error getting host node info for {server}. Exception: {ex}")
        return None

    # if well-known nodeinfo isn't found, try to check host-meta for a webfinger URL
    # needed on servers where the display domain is different than the web domain
    if resp.status_code != 200 and not host_meta_fallback:
        # not found, try to check host-meta as a fallback
        logger.debug(f'nodeinfo for {server} not found, checking host-meta')
        new_server = get_server_from_host_meta(server, http=http)
        if new_server is None:
            return None
        if new_server == server:
            logger.debug(f'host-meta for {server} did not get a new server.')
            return None
        return get_nodeinfo(new_server, cache, True, http=http)

    if resp.status_code != 200:
        logger.error(f'Error getting well-known host node info for {server}. Status Code: {resp.status_code}')
        return None

    node_location = None
    try:
        for link in resp.json()['links']:
            if link['rel'] in NODEINFO_SCHEMAS:
                node_location = link['href']
                break
    except Exception as ex:
        logger.error(f'error getting server {server} info from well-known node info. Exception: {ex}')
        return None

    if node_location is None:
        logger.error(f'could not find link to node info in well-known nodeinfo of {server}')
        return None

    # regrab server from node_location, again in the case of different display and web domains
    match = re.match(r"https://(?P<server>[^/]+)/", node_location)
    if match is None:
        logger.error(f"Error getting web server name from {server}.")
        return None

    server = match.group('server')

    # return early if the web domain has been seen previously (in cases with host-meta lookups)
    if server in cache:
        return ServerInfo.from_state(cache.get(server))

    try:
        resp = http.get(node_location, timeout = 30)
    except Exception as ex:
        logger.error(f"Error getting host node info for {server}. Exception: {ex}")
        return None

    if resp.status_code != 200:
        logger.error(f'Error getting host node info for {server}. Status Code: {resp.status_code}')
        return None

    try:
        nodeinfo = resp.json()
        if 'activitypub' not in nodeinfo['protocols']:
            logger.warning(f'server {server} does not support activitypub, skipping')
            return None
        return ServerInfo.from_nodeinfo(server, nodeinfo)
    except Exception as ex:
        logger.error(f'error getting server {server} info from nodeinfo. Exception: {ex}')
        return None


def get_server_from_host_meta(server: str, *, http: HttpClient) -> str | None:
    url = f'https://{server}/.well-known/host-meta'
    try:
        resp = http.get(url, timeout = 30)
    except Exception as ex:
        logger.error(f"Error getting host meta for {server}. Exception: {ex}")
        return None

    if resp.status_code != 200:
        logger.error(f'Error getting host meta for {server}. Status Code: {resp.status_code}')
        return None

    try:
        host_meta = ET.fromstring(resp.text)
        lrdd = host_meta.find('.//{http://docs.oasis-open.org/ns/xri/xrd-1.0}Link[@rel="lrdd"]')
        match = re.match(r"https://(?P<server>[^/]+)/", lrdd.get('template'))
        if match is None:
            raise Exception(f'server not found in lrdd for {server}')
        return match.group("server")
    except Exception as ex:
        logger.error(f'Error parsing host meta for {server}. Exception: {ex}')
        return None
