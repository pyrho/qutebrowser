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

"""The main browser widgets."""

import functools

from PyQt5.QtCore import pyqtSignal, pyqtSlot, PYQT_VERSION, Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtNetwork import QNetworkReply
from PyQt5.QtWidgets import QFileDialog
from PyQt5.QtPrintSupport import QPrintDialog
from PyQt5.QtWebKitWidgets import QWebPage

from qutebrowser.config import config
from qutebrowser.network import networkmanager
from qutebrowser.utils import (message, usertypes, log, http, jinja, qtutils,
                               utils, objreg)


class BrowserPage(QWebPage):

    """Our own QWebPage with advanced features.

    Attributes:
        _extension_handlers: Mapping of QWebPage extensions to their handlers.
        _networkmnager: The NetworkManager used.
        _win_id: The window ID this BrowserPage is associated with.

    Signals:
        start_download: Emitted when a file should be downloaded.
    """

    start_download = pyqtSignal('QNetworkReply*')

    def __init__(self, win_id, parent=None):
        super().__init__(parent)
        self._win_id = win_id
        self._extension_handlers = {
            QWebPage.ErrorPageExtension: self._handle_errorpage,
            QWebPage.ChooseMultipleFilesExtension: self._handle_multiple_files,
        }
        self._networkmanager = networkmanager.NetworkManager(win_id, self)
        self.setNetworkAccessManager(self._networkmanager)
        self.setForwardUnsupportedContent(True)
        self.printRequested.connect(self.on_print_requested)
        self.downloadRequested.connect(self.on_download_requested)
        self.unsupportedContent.connect(self.on_unsupported_content)

    if PYQT_VERSION > 0x050300:
        # WORKAROUND (remove this when we bump the requirements to 5.3.1)
        # We can't override javaScriptPrompt with older PyQt-versions because
        # of a bug in PyQt.
        # See http://www.riverbankcomputing.com/pipermail/pyqt/2014-June/034385.html

        def javaScriptPrompt(self, _frame, msg, default):
            """Override javaScriptPrompt to use the statusbar."""
            answer = message.ask(self._win_id, "js: {}".format(msg),
                                 usertypes.PromptMode.text, default)
            if answer is None:
                return (False, "")
            else:
                return (True, answer)

    def _handle_errorpage(self, info, errpage):
        """Display an error page if needed.

        Loosly based on Helpviewer/HelpBrowserWV.py from eric5
        (line 260 @ 5d937eb378dd)

        Args:
            info: The QWebPage.ErrorPageExtensionOption instance.
            errpage: The QWebPage.ErrorPageExtensionReturn instance, where the
                     error page will get written to.

        Return:
            False if no error page should be displayed, True otherwise.
        """
        ignored_errors = [
            (QWebPage.QtNetwork, QNetworkReply.OperationCanceledError),
            (QWebPage.WebKit, 203),  # "Loading is handled by the media engine"
        ]
        errpage.baseUrl = info.url
        urlstr = info.url.toDisplayString()
        if (info.domain, info.error) == (QWebPage.QtNetwork,
                                         QNetworkReply.ProtocolUnknownError):
            # For some reason, we get a segfault when we use
            # QDesktopServices::openUrl with info.url directly - however it
            # works when we construct a copy of it.
            url = QUrl(info.url)
            msg = "Open external application for {}-link?\nURL: {}".format(
                url.scheme(), url.toDisplayString())
            message.confirm_async(
                self._win_id, msg,
                functools.partial(QDesktopServices.openUrl, url))
            return True
        elif (info.domain, info.error) in ignored_errors:
            log.webview.debug("Ignored error on {}: {} (error domain: {}, "
                              "error code: {})".format(
                                  urlstr, info.errorString, info.domain,
                                  info.error))
            return False
        else:
            log.webview.error("Error while loading {}: {}".format(
                urlstr, info.errorString))
            log.webview.debug("Error domain: {}, error code: {}".format(
                info.domain, info.error))
            title = "Error loading page: {}".format(urlstr)
            template = jinja.env.get_template('error.html')
            html = template.render(  # pylint: disable=maybe-no-member
                title=title, url=urlstr, error=info.errorString, icon='')
            errpage.content = html.encode('utf-8')
            errpage.encoding = 'utf-8'
            return True

    def _handle_multiple_files(self, info, files):
        """Handle uploading of multiple files.

        Loosly based on Helpviewer/HelpBrowserWV.py from eric5.

        Args:
            info: The ChooseMultipleFilesExtensionOption instance.
            files: The ChooseMultipleFilesExtensionReturn instance to write
                   return values to.

        Return:
            True on success, the superclass return value on failure.
        """
        suggested_file = ""
        if info.suggestedFileNames:
            suggested_file = info.suggestedFileNames[0]
        files.fileNames, _ = QFileDialog.getOpenFileNames(None, None,
                                                          suggested_file)
        return True

    def display_content(self, reply, mimetype):
        """Display a QNetworkReply with an explicitely set mimetype."""
        self.mainFrame().setContent(reply.readAll(), mimetype, reply.url())
        reply.deleteLater()

    def on_print_requested(self, frame):
        """Handle printing when requested via javascript."""
        if not qtutils.check_print_compat():
            message.error(self._win_id, "Printing on Qt < 5.3.0 on Windows is "
                          "broken, please upgrade!", immediately=True)
            return
        printdiag = QPrintDialog()
        printdiag.setAttribute(Qt.WA_DeleteOnClose)
        printdiag.open(lambda: frame.print(printdiag.printer()))

    @pyqtSlot('QNetworkRequest')
    def on_download_requested(self, request):
        """Called when the user wants to download a link."""
        reply = self.networkAccessManager().get(request)
        self.start_download.emit(reply)

    @pyqtSlot('QNetworkReply')
    def on_unsupported_content(self, reply):
        """Handle an unsupportedContent signal.

        Most likely this will mean we need to download the reply, but we
        correct for some common errors the server do.

        At some point we might want to implement the MIME Sniffing standard
        here: http://mimesniff.spec.whatwg.org/
        """
        inline, _suggested_filename = http.parse_content_disposition(reply)
        if not inline:
            # Content-Disposition: attachment -> force download
            self.start_download.emit(reply)
            return
        mimetype, _rest = http.parse_content_type(reply)
        if mimetype == 'image/jpg':
            # Some servers (e.g. the LinkedIn CDN) send a non-standard
            # image/jpg (instead of image/jpeg, defined in RFC 1341 section
            # 7.5). If this is the case, we force displaying with a corrected
            # mimetype.
            if reply.isFinished():
                self.display_content(reply, 'image/jpeg')
            else:
                reply.finished.connect(functools.partial(
                    self.display_content, reply, 'image/jpeg'))
        else:
            # Unknown mimetype, so download anyways.
            self.start_download.emit(reply)

    def userAgentForUrl(self, url):
        """Override QWebPage::userAgentForUrl to customize the user agent."""
        ua = config.get('network', 'user-agent')
        if ua is None:
            return super().userAgentForUrl(url)
        else:
            return ua

    def supportsExtension(self, ext):
        """Override QWebPage::supportsExtension to provide error pages.

        Args:
            ext: The extension to check for.

        Return:
            True if the extension can be handled, False otherwise.
        """
        return ext in self._extension_handlers

    # WORKAROUND for:
    # http://www.riverbankcomputing.com/pipermail/pyqt/2014-August/034722.html
    @utils.prevent_exceptions(False, PYQT_VERSION < 0x50302)
    def extension(self, ext, opt, out):
        """Override QWebPage::extension to provide error pages.

        Args:
            ext: The extension.
            opt: Extension options instance.
            out: Extension output instance.

        Return:
            Handler return value.
        """
        try:
            handler = self._extension_handlers[ext]
        except KeyError:
            log.webview.warning("Extension {} not supported!".format(ext))
            return super().extension(ext, opt, out)
        return handler(opt, out)

    def javaScriptAlert(self, _frame, msg):
        """Override javaScriptAlert to use the statusbar."""
        message.ask(self._win_id, "[js alert] {}".format(msg),
                    usertypes.PromptMode.alert)

    def javaScriptConfirm(self, _frame, msg):
        """Override javaScriptConfirm to use the statusbar."""
        ans = message.ask(self._win_id, "[js confirm] {}".format(msg),
                          usertypes.PromptMode.yesno)
        return bool(ans)

    def javaScriptConsoleMessage(self, msg, line, source):
        """Override javaScriptConsoleMessage to use debug log."""
        log.js.debug("[{}:{}] {}".format(source, line, msg))

    def chooseFile(self, _frame, suggested_file):
        """Override QWebPage's chooseFile to be able to chose a file to upload.

        Args:
            frame: The parent QWebFrame.
            suggested_file: A suggested filename.
        """
        filename, _ = QFileDialog.getOpenFileName(None, None, suggested_file)
        return filename

    def shouldInterruptJavaScript(self):
        """Override shouldInterruptJavaScript to use the statusbar."""
        answer = message.ask(self._win_id, "Interrupt long-running "
                             "javascript?", usertypes.PromptMode.yesno)
        if answer is None:
            answer = True
        return answer

    def acceptNavigationRequest(self, _frame, request, typ):
        """Override acceptNavigationRequest to handle clicked links.

        Setting linkDelegationPolicy to DelegateAllLinks and using a slot bound
        to linkClicked won't work correctly, because when in a frameset, we
        have no idea in which frame the link should be opened.

        Checks if it should open it in a tab (middle-click or control) or not,
        and then opens the URL.

        Args:
            _frame: QWebFrame (target frame)
            request: QNetworkRequest
            typ: QWebPage::NavigationType
        """
        if typ != QWebPage.NavigationTypeLinkClicked:
            return True
        url = request.url()
        urlstr = url.toDisplayString()
        if not url.isValid():
            message.error(self._win_id, "Invalid link {} clicked!".format(
                urlstr))
            log.webview.debug(url.errorString())
            return False
        tabbed_browser = objreg.get('tabbed-browser', scope='window',
                                    window=self._win_id)
        open_target = self.view().open_target
        if open_target == usertypes.ClickTarget.tab:
            tabbed_browser.tabopen(url, False)
            return False
        elif open_target == usertypes.ClickTarget.tab_bg:
            tabbed_browser.tabopen(url, True)
            return False
        elif open_target == usertypes.ClickTarget.window:
            main_window = objreg.get('main-window', scope='window',
                                     window=self._win_id)
            win_id = main_window.spawn()
            tabbed_browser = objreg.get('tabbed-browser', scope='window',
                                        window=win_id)
            tabbed_browser.tabopen(url, False)
            return False
        else:
            return True
