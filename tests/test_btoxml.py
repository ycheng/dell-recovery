# -*- coding: utf-8 -*-
#!/usr/bin/env python
#
# @copyright 2011 Canonical Ltd.
# Author 2011 Hsin-Yi Chen
#
# This is a free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# This software is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this software; if not, write to the Free Software Foundation, Inc., 59 Temple
# Place, Suite 330, Boston, MA 02111-1307 USA
import os
import unittest
import tempfile

from Dell import recovery_xml

non_ascii_string = ''.join(map(chr, range(129,255)))

class BTOxmlTestCase(unittest.TestCase):

    def setUp(self):
        self.xmlpath = tempfile.mktemp()

    def tearDown(self):
        os.remove(self.xmlpath)

    def _write(self, lines):
        lines = ['<?xml version="1.0" ?><bto>'] + lines + ['</bto>']
        with open(self.xmlpath, 'w') as f:
            f.write('\n'.join(lines))

    def _write_node(self, datas):
        self.wxmlobj = recovery_xml.BTOxml()
        for e in datas:
            (tag, val) = e
            self.wxmlobj.replace_node_contents(tag, val)
        self.wxmlobj.write_xml(self.xmlpath)

    def _read_node(self, tag):
        self.rxmlobj = recovery_xml.BTOxml()
        self.rxmlobj.load_bto_xml(self.xmlpath)
        return self.rxmlobj.fetch_node_contents(tag)

class ReadWriteNewBTOxmlTestCase(BTOxmlTestCase):

    def test_read_with_ascii(self):
        self._write_node([('syslog', '123')])
        self.assertEquals('123', self._read_node('syslog'))

    def test_read_with_nonascii(self):
        self._write_node([('syslog', non_ascii_string)])
        self.assertEquals(unicode(non_ascii_string, errors='replace'),
                          self._read_node('syslog'))

    def test_read_with_mix(self):
        self._write_node([('date', '2011-11-22'), ('syslog', non_ascii_string)])
        self.assertEquals(u'2011-11-22', self._read_node('date'))

class ReadWriteExistedBTOxmlTestCase(BTOxmlTestCase):

    def test_read_with_ascii(self):
        lines = [
            '<date>2011-11-22</date>',
            '<syslog>2011-11-22</syslog>',
        ]
        self._write(lines)
        self.assertEquals('2011-11-22', self._read_node('date'))
        self.assertEquals('2011-11-22', self._read_node('syslog'))

    def test_read_with_nonascii(self):
        lines = [
            '<date>2011-11-22</date>',
            '<syslog>{}</syslog>'.format(non_ascii_string)
        ]
        self._write(lines)
        self.assertEquals(unicode(non_ascii_string, errors='replace'),
                          self._read_node('syslog'))

def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(ReadWriteNewBTOxmlTestCase, 'test'))
    suite.addTest(unittest.makeSuite(ReadWriteExistedBTOxmlTestCase, 'test'))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
