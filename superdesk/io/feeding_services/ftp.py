# -*- coding: utf-8; -*-
#
# This file is part of Superdesk.
#
# Copyright 2013, 2014 Sourcefabric z.u. and contributors.
#
# For the full copyright and license information, please see the
# AUTHORS and LICENSE files distributed with this source code, or
# at https://www.sourcefabric.org/superdesk/license


import os
import ftplib
import logging
import tempfile

from datetime import datetime

from superdesk.io.registry import register_feeding_service
from superdesk.io.feed_parsers import XMLFeedParser
from superdesk.utc import utc
from superdesk.etree import etree
from superdesk.io.feeding_services import FeedingService
from superdesk.errors import IngestFtpError
from superdesk.ftp import ftp_connect
from superdesk.io.commands.update_ingest import LAST_UPDATED

try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

logger = logging.getLogger(__name__)


class FTPFeedingService(FeedingService):
    """
    Feeding Service class which can read article(s) which exist in a file system and accessible using FTP.
    """

    NAME = 'ftp'
    ERRORS = [IngestFtpError.ftpUnknownParserError().get_error_description(),
              IngestFtpError.ftpError().get_error_description()]

    FILE_SUFFIX = '.xml'
    DATE_FORMAT = '%Y%m%d%H%M%S'

    def config_from_url(self, url):
        """
        Parse given url into ftp config.

        :param url: url in form `ftp://username:password@host:port/dir`
        """
        url_parts = urlparse(url)
        return {
            'username': url_parts.username,
            'password': url_parts.password,
            'host': url_parts.hostname,
            'path': url_parts.path.lstrip('/'),
        }

    def _update(self, provider, update):
        config = provider.get('config', {})
        last_updated = provider.get('last_updated')
        crt_last_updated = None

        if 'dest_path' not in config:
            config['dest_path'] = tempfile.mkdtemp(prefix='superdesk_ingest_')

        try:
            with ftp_connect(config) as ftp:
                items = []
                for filename, facts in ftp.mlsd():
                    if facts.get('type', '') != 'file':
                        continue

                    if not filename.lower().endswith(self.FILE_SUFFIX):
                        continue

                    if last_updated:
                        item_last_updated = datetime.strptime(facts['modify'], self.DATE_FORMAT).replace(tzinfo=utc)
                        if item_last_updated < last_updated:
                            continue
                        elif not crt_last_updated or item_last_updated > crt_last_updated:
                            crt_last_updated = item_last_updated

                    local_file_path = os.path.join(config['dest_path'], filename)
                    try:
                        with open(local_file_path, 'xb') as f:
                            try:
                                ftp.retrbinary('RETR %s' % filename, f.write)
                            except ftplib.all_errors as ex:
                                os.remove(local_file_path)
                                logger.exception('Exception retrieving file from FTP server (%s)', filename)
                                continue
                    except FileExistsError:
                        logger.error('Exception retrieving from FTP server, file already exists (%s)', local_file_path)
                        continue

                    registered_parser = self.get_feed_parser(provider)
                    if isinstance(registered_parser, XMLFeedParser):
                        xml = etree.parse(local_file_path).getroot()
                        parser = self.get_feed_parser(provider, xml)
                        parsed = parser.parse(xml, provider)
                    else:
                        parser = self.get_feed_parser(provider, local_file_path)
                        parsed = parser.parse(local_file_path, provider)

                    if isinstance(parsed, dict):
                        parsed = [parsed]

                    items.append(parsed)
            if crt_last_updated:
                update[LAST_UPDATED] = crt_last_updated
            return items
        except IngestFtpError:
            raise
        except Exception as ex:
            raise IngestFtpError.ftpError(ex, provider)


register_feeding_service(FTPFeedingService.NAME, FTPFeedingService(), FTPFeedingService.ERRORS)
