import os
import sys

sys.path.insert(0, os.getcwd())
import main
import server
from http.server import ThreadingHTTPServer

main.setup_database()
httpd = ThreadingHTTPServer(('127.0.0.1', 8000), server.Handler)
print('listening on 8000')
httpd.serve_forever()
