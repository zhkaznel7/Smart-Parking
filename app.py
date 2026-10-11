"""Flask entry point for Smart Parking."""
import logging
import os
import re

from flask import Flask, jsonify, redirect, request
from parking_portal import register_smart_parking


class PrivateLinkLogFilter(logging.Filter):
    """Keep private parking links out of the HTTP access log."""

    def filter(self, record):
        record.msg = re.sub(r'/my-parking/[A-Za-z0-9_-]+', '/my-parking/[private]', record.getMessage())
        record.args = ()
        return True


logging.basicConfig(level=logging.INFO)
logging.getLogger('werkzeug').addFilter(PrivateLinkLogFilter())

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(32)

# Preserve old bookmarks, but never run the original booking/authentication code.
LEGACY_ROUTES = [
    ('/new-user-registration-system', ['GET']),
    ('/admin-login', ['GET', 'POST']),
    ('/admin-dashboard', ['GET']),
    ('/logout', ['GET']),
    ('/admin-users', ['GET']),
    ('/register_user', ['GET', 'POST']),
    ('/user-login', ['GET', 'POST']),
    ('/profile/<username>', ['GET']),
    ('/edit-profile/<username>', ['GET', 'POST']),
    ('/user-booking/<username>', ['GET']),
    ('/user-logout', ['GET']),
    ('/book-slot/<lot_id>/<slot_id>', ['POST']),
    ('/unbook-slot/<lot_id>/<slot_id>', ['POST']),
    ('/user/unbook-receipt/<int:booking_id>', ['GET']),
    ('/user/unbook-parking/<int:booking_id>', ['POST']),
    ('/summery/<username>', ['GET']),
    ('/admin/parking/edit/<int:lot_id>', ['GET', 'POST']),
    ('/manage-slot/<int:slot_id>', ['GET', 'POST']),
    ('/admin/parking/delete/<int:lot_id>', ['POST']),
    ('/admin/parking/delete-confirm/<int:lot_id>', ['GET', 'POST']),
    ('/admin-view-booking/<lot_id>/<slot_id>', ['GET']),
    ('/user/<username>/booking-history', ['GET']),
    ('/admin-summery', ['GET']),
    ('/add-parking-lot', ['GET']),
    ('/submit-location', ['POST']),
    ('/booking/<int:slot_id>', ['GET']),
]


def legacy_route(**params):
    if request.method in ('GET', 'HEAD'):
        target = '/admin' if request.path.startswith(('/admin', '/dashboard', '/vehicles', '/history')) else '/'
        return redirect(target)
    return jsonify(error='Используйте новый интерфейс парковки.'), 410


for index, (path, methods) in enumerate(LEGACY_ROUTES):
    app.add_url_rule(path, endpoint=f'legacy_{index}', view_func=legacy_route,
                     methods=[*methods, 'OPTIONS'], provide_automatic_options=False)

register_smart_parking(app)

if __name__ == '__main__':
    app.run(host=os.environ.get('HOST', '127.0.0.1'),
            port=int(os.environ.get('PORT', '5000')),
            debug=os.environ.get('FLASK_DEBUG') == '1')
