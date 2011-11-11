""" A set of stream oriented parsers for http requests and responses, 
inline with the current draft recommendations from the http working group.

http://tools.ietf.org/html/draft-ietf-httpbis-p1-messaging-17

Unlike other libraries, this is for clients, servers and proxies.
"""

from StringIO import StringIO
class ParseError(StandardError):
    pass

from .semantics import Codes, Methods

CRLF = '\r\n'

"""
Missing:
    Parsing Trailing Headers as part of the request
    comma parsing/header folding

"""

class HTTPParser(object):
    """A stream based parser for http like messages"""
    def __init__(self, buffer, header):
        self.buffer = buffer
        self.offset = self.buffer.tell()
        self.header = header
        
        self.body_offset = -1
        self.mode = 'start'
        self.body_reader = None

    def feed(self, text):
        if text and self.mode == 'start':
            text = self.feed_start(text)

        if text and self.mode == 'headers':
           text = self.feed_headers(text)
           if self.mode == 'body':
                if not self.header.has_body():
                    self.mode = 'end'
                else:
                    self.body_offset = self.offset
                    if self.header.body_is_chunked():
                        self.body_reader = ChunkReader()
                    else:
                        len = self.header.body_length()
                        if len is not None:
                            self.body_reader = LengthReader(len)
                        else:
                            self.body_reader = None

        if text and self.mode == 'body':
            if self.body_reader is not None:
                 text = self.body_reader.feed(self, text)
            else:
                self.buffer.write(text)
                text = ''

        return text

    def close(self):
        if self.mode =='start' or (self.body_reader is None and self.mode == 'body'):
            self.mode = 'end'
        
        elif self.mode != 'end':
            self.mode = 'incomplete'


    def headers_complete(self):
        return self.mode in ('end', 'body')

    def complete(self):
        return self.mode =='end'

    def feed_line(self, text):
        """ feed text into the buffer, returning the first line found (if found yet)"""
        #print 'feed line', repr(text)
        line = None
        nl= text.find(CRLF)
        if nl > -1:
            nl+=2
            self.buffer.write(text[:nl])
            self.buffer.seek(self.offset)
            line = self.buffer.readline()
            self.offset = self.buffer.tell()
            text = text[nl:]
        else:
            self.buffer.write(text)
            text = ''
        #print 'feed line', repr(line), repr(text)
        return line, text

    def feed_length(self, text, remaining):
        """ feed (at most remaining bytes) text to buffer, returning leftovers """
        body, text = text[:remaining], text[remaining:]
        remaining -= len(body)
        self.buffer.write(body)
        self.offset = self.buffer.tell()
        return remaining, text

    def feed_start(self, text):
        line, text = self.feed_line(text)
        if line is not None:
            if line != CRLF: # skip leading newlines
                self.header.set_start_line(line)
                self.mode = 'headers'

        return text
                
    def feed_headers(self, text):
        while text:
            line, text = self.feed_line(text)
            if line is not None:
                self.header.add_header(line)
                if line == CRLF:
                    self.mode = 'body'
                    break

        return text

            
class ChunkReader(object):
    def __init__(self):
        self.mode = "start"
        self.remaining = 0

    def feed(self, parser, text):
        while text:
            if self.mode == 'start':
                #print self.mode, repr(text)
                
                line, text = parser.feed_line(text)
                if line is not None:
                    chunk = int(line.split(';',1)[0], 16)
                    self.remaining = chunk
                    if chunk == 0:
                        self.mode = 'trailer'
                    else:
                        self.mode = 'chunk'
                #print self.mode, repr(text)

            if text and self.mode == 'chunk':
                #print self.mode, repr(text), self.remaining
                if self.remaining > 0: 
                    self.remaining, text = parser.feed_length(text, self.remaining)
                if self.remaining == 0:
                    end_of_chunk, text = parser.feed_line(text)
                    #print 'end',end_of_chunk
                    if end_of_chunk:
                        #print 'ended'
                        self.mode = 'start'
                #print self.mode, repr(text)

            if text and self.mode == 'trailer':
                line, text = parser.feed_line(text)
                if line is not None:
                    parser.header.add_trailer(line)
                    if line == CRLF:
                        self.mode = 'end'

            if self.mode == 'end':
                parser.mode ='end'
                break

        return text

class LengthReader(object):
    def __init__(self, length):
        self.remaining = length

    def feed(self, parser, text):
        if self.remaining > 0: 
            self.remaining, text = parser.feed_length(text, self.remaining)
        if self.remaining <= 0:
            parser.mode ='end'
        return text
            

class HTTPHeader(object):
    def __init__(self):
        self.headers = []
        self.keep_alive = True
        self.mode = 'close'
        self.content_length = None
        self.encoding = None
        self.trailers = []

    def has_body(self):
        pass

    def set_start_line(self, line):
        pass

    def add_trailer(self, line):
        if line.startswith(' ') or line.startswith('\t'):
            k,v = self.trailers.pop()
            line = line.strip()
            v = "%s %s"%(v, line)
            self.trailers.append((k,v))
        elif line == '\r\n':
            pass
        else:
            name, value = line.split(':',1)
            name = name.strip()
            value = value.strip()
            self.trailers.append((name, value))
        
    def add_header(self, line):
        if line.startswith(' ') or line.startswith('\t'):
            k,v = self.headers.pop()
            line = line.strip()
            v = "%s %s"%(v, line)
            self.headers.append((k,v))
        
        elif line == '\r\n':
            for name, value in self.headers:
                name = name.lower()
                value = value.lower()


                # todo handle multiple instances
                # of these headers
                if name == 'content-length':
                    if self.mode == 'close':
                        self.content_length = int(value)
                        self.mode = 'length'
                        
                if name == 'transfer-encoding':
                    if 'chunked' in value:
                        self.mode = 'chunked'

                if name == 'content-encoding':
                    self.encoding = value

                if name == 'connection':
                    if 'keep-alive' in value:
                        self.keep_alive = True
                    elif 'close' in value:
                        self.keep_alive = False

            if self.mode == 'close':
                self.keep_alive = False

        else:
            #print line
            name, value = line.split(':',1)
            name = name.strip()
            value = value.strip()
            self.headers.append((name, value))
    
    def body_is_chunked(self):
        return self.mode == 'chunked'

    def body_length(self):
        if self.mode == 'length':
            return self.content_length

class RequestHeader(HTTPHeader):
    def __init__(self):
        HTTPHeader.__init__(self)
        self.method = ''
        self.target_uri = ''
        self.version = ''

    def set_start_line(self, line):
        self.method, self.target_uri, self.version = line.rstrip().split(' ',2)
        if self.version =='HTTP/1.0':
            self.keep_alive = False

    def has_body(self):
        return self.mode in ('chunked', 'length')

class ResponseHeader(HTTPHeader):
    def __init__(self, request):
        HTTPHeader.__init__(self)
        self.request = request
        self.version = None
        self.code = None
        self.phrase = None

    def set_start_line(self, line):
        self.version, self.code, self.phrase = line.rstrip().split(' ',2)
        self.code = int(self.code)
        if self.version =='HTTP/1.0':
            self.keep_alive = False

    def has_body(self):
        if self.request.method in Methods.no_body:
            return False
        elif self.code in Codes.no_body:
            return False
        elif self.mode == 'chunked':
            return True
        elif self.mode == 'length':
            return self.content_length > 0

        # no length, wait for connection close
        return True


class RequestParser(HTTPParser):
    def __init__(self, buffer):
        HTTPParser.__init__(self, buffer, RequestHeader())

class ResponseParser(HTTPParser):
    def __init__(self, buffer, request_header):
        self.interim = []
        HTTPParser.__init__(self, buffer, ResponseHeader(request_header))

    def feed(self, text):
        text = HTTPParser.feed(self, text)
        if self.complete() and self.header.code == Codes.Continue:
            self.interim.append(self.header)
            self.header = ResponseHeader(self.header.request)
            self.body_offset = -1
            self.mode = 'start'
            self.body_reader = None
            text = HTTPParser.feed(self, text)
        return text
            