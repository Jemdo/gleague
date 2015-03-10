import json

from flask import Blueprint, g, abort, current_app, render_template, request, current_app
from sqlalchemy import desc, func, and_, case

from ..models import Player, PlayerMatchStats, SeasonStats, Season
from ..core import db
from . import login_required, admin_required


players_bp = Blueprint('players', __name__)


@players_bp.route('/<int:steam_id>/', methods=['GET'])
@players_bp.route('/<int:steam_id>/overview', methods=['GET'])
def player_overview(steam_id):
    p = Player.query.get(steam_id)
    if not p:
        return abort(404)
    cs_id = Season.current().id
    stats = PlayerMatchStats.query.join(SeasonStats).filter(SeasonStats.steam_id==steam_id)\
        .order_by(desc(PlayerMatchStats.match_id)).limit(8)
    pts_seq = PlayerMatchStats.query.join(SeasonStats).filter(and_(SeasonStats.season_id==cs_id,
        SeasonStats.steam_id==steam_id)).order_by(PlayerMatchStats.match_id)\
        .values(PlayerMatchStats.old_pts+PlayerMatchStats.pts_diff)
    pts_hist = [[0, 1000]]
    for index, el in enumerate(pts_seq):
        pts_hist.append([index+1, el[0]])
    rating_info = p.get_avg_rating()[0]
    avg_rating = rating_info[0] or 0
    rating_amount = rating_info[1]
    signature_heroes = p.get_signature_heroes()
    matches_stats = stats.all()
    return render_template('player_overview.html', player = p, avg_rating=avg_rating,
        rating_amount=rating_amount, signature_heroes=signature_heroes, matches_stats=matches_stats,
        pts_history=json.dumps(pts_hist))


@players_bp.route('/<int:steam_id>/matches', methods=['GET'])
@players_bp.route('/<int:steam_id>/matches', methods=['GET'])
def player_matches(steam_id):
    p = Player.query.get(steam_id)
    if not p:
        return abort(404)
    _args = {'player': p}
    page = request.args.get('page', '1')
    if not page.isdigit():
        abort(400)
    page = int(page)
    hero_filter = request.args.get('hero', None)
    cs_id = Season.current().id
    matches_stats = PlayerMatchStats.query.order_by(desc(PlayerMatchStats.match_id))\
        .join(SeasonStats).filter(SeasonStats.steam_id==steam_id)
    if hero_filter:
        _args['hero_filter'] = hero_filter
        matches_stats = matches_stats.filter(PlayerMatchStats.hero==hero_filter)
    _args['matches_stats'] = matches_stats.paginate(page, 
        current_app.config['PLAYER_HISTORY_MATCHES_PER_PAGE'], True)
    rating_info = p.get_avg_rating()[0]
    _args['avg_rating'] = rating_info[0] or 0
    _args['rating_amount'] = rating_info[1]
    return render_template('player_matches.html', **_args)
