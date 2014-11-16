# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2014 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""Class to handle web history"""

from PyQt5.QtWebKit import QWebHistoryInterface
from PyQt5.QtCore import pyqtSignal, QObject, QStandardPaths
from qutebrowser.utils import objreg, log, standarddir
from qutebrowser.config import lineparser
import collections

class HistoryManager(QObject):

    """Manager for navigation history
    """

    item_added = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # TODO: save/load history from file
        # For completion purpuse, it's best to use a set as
        # lookup (O(1)) is more efficient than a list (O(n)).
        # If we also wanted to keep a real history of navigation
        # a list would be helpful
        # http://stackoverflow.com/a/11241481 This says that dicts are faster than
        # sets.

        self.history = collections.OrderedDict()
        confdir = standarddir.get(QStandardPaths.ConfigLocation)
        self._linecp = lineparser.LineConfigParser(confdir, 'history')
        for line in self._linecp:
            self.history[line] = None

    def save(self):
        """Save the history to disk."""
        self._linecp.data = [''.join(h) for h in self.history]
        self._linecp.save()
    
    def getHistory(self):
        log.history.info("Getting history, current # of entries %s" % len(self.history))
        return self.history

    def addToHistory(self, url):
        if url not in self.history:
            log.history.debug("Adding %s to history" % url)
            self.history[url] = None
            self.item_added.emit(url)
        else:
            log.history.debug("Not Adding %s to history" % url)



class WebHistoryInterface(QWebHistoryInterface):

    @staticmethod
    def setDefaultInterface():
        QWebHistoryInterface.setDefaultInterface(WebHistoryInterface())

    def __init__(self):
        super(QWebHistoryInterface, self).__init__()

    def addHistoryEntry(self, url):
        """ The history is shared amongst all QWebPage
        Each QWebpage will call this function so that we
        can keep track of browsing history
        """
        objreg.get('history-manager').addToHistory(url)

    def historyContains(self, url):
        """ Not sure this is useful for us """
        return False


