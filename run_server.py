import os
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main
import server

main.setup_database()
server.startup_security()
httpd = ThreadingHTTPServer(("127.0.0.1", server.PORT), server.Handler)
print("Open " + server.EXPECTED_ORIGIN + " in this computer's browser.")
httpd.serve_forever()
