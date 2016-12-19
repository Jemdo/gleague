import json
import os
import tempfile

from sqlalchemy import (Column, String, Integer, ForeignKey, Boolean, BigInteger, SmallInteger, desc, func, 
    and_, or_)
from sqlalchemy.schema import CheckConstraint, UniqueConstraint
from sqlalchemy.orm import relationship
from flask import current_app
from subprocess import Popen, PIPE

from gleague.core import db
from gleague.models.dota.season import DotaSeasonStats
from gleague.utils.steam_api import get_dota2_heroes


class DotaPlayerMatchStats(db.Model):
    __tablename__ = 'player_match_stats'

    id = Column(Integer, primary_key=True)
    season_stats_id = Column(Integer, ForeignKey('season_stats.id', onupdate="CASCADE", 
                                                    ondelete="CASCADE"), nullable=False)
    match_id = Column(BigInteger, ForeignKey('match.id', onupdate="CASCADE", 
                                                    ondelete="CASCADE"), nullable=False)
    old_pts = Column(Integer, nullable=False)
    pts_diff = Column(Integer, nullable=False)
    kills = Column(Integer, nullable=False)
    assists = Column(Integer, nullable=False)
    deaths = Column(Integer, nullable=False)
    hero_damage = Column(Integer, nullable=False)
    last_hits = Column(Integer, nullable=False)
    player_slot = Column(Integer, nullable=False)
    denies = Column(Integer, nullable=False)
    tower_damage = Column(Integer)
    hero = Column(String(255), nullable=False)
    hero_healing = Column(Integer)
    level = Column(Integer, nullable=False)
    xp_per_min = Column(Integer, nullable=True)
    gold_per_min = Column(Integer, nullable=True)
    damage_taken = Column(Integer, nullable=True)
    player_match_ratings = relationship('DotaPlayerMatchRating', cascade="all, delete", 
                                                            backref="player_match_stats")

    def __repr__(self):
        return '%s (%s) -- Match %s' % (self.season_stats.player.nickname, self.season_stats.steam_id, self.match_id)

    def to_dict(self, extensive=True, **kwargs):
        with_season_stats = kwargs.get('with_season_stats', False)
        d = {
            'id': self.id,
            'player_slot': self.player_slot,
            'self.hero': self.hero
        }
        if extensive:
            d.update({
                'old_pts': self.old_pts,
                'kills': self.kills,
                'assists': self.assists,
                'deaths': self.deaths,
                'hero_damage': self.hero_damage,
                'denies': self.denies,
                'tower_damage': self.tower_damage,
                'self.hero_healing': self.hero_healing,
                'self.level': self.level
            })
            if with_season_stats:
                d['season_stats'] = self.season_stats.to_dict(True)
        else:
            d.update({
                'player_nickname': self.season_stats.player.nickname
            })
        return d


class DotaPlayerMatchRating(db.Model):
    __tablename__ = 'player_match_rating'

    id = Column(Integer, primary_key=True)
    rated_by_steam_id = Column(BigInteger, ForeignKey('player.steam_id', onupdate="CASCADE", 
                                                                ondelete="CASCADE"), nullable=False)
    rating = Column(SmallInteger, default=5)
    player_match_stats_id = Column(Integer, ForeignKey('player_match_stats.id', onupdate="CASCADE", 
                                                                ondelete="CASCADE"), nullable=False)

    __table_args__ = (
        CheckConstraint('player_match_rating.rating >= 1'),
        CheckConstraint('player_match_rating.rating <= 5'),
        UniqueConstraint('rated_by_steam_id', 'player_match_stats_id', name='rated_once')
    )

    @staticmethod
    def get_match_ratings(match_id, user_id=None):
        from gleague.models.dota.season import DotaSeasonStats
        # TODO: pure sqlalchemy
        rated_by_ps = played = None
        if user_id:
            rated_by_ps = DotaPlayerMatchStats.query.join(DotaSeasonStats).filter(and_(DotaSeasonStats.steam_id==user_id, 
                DotaPlayerMatchStats.match_id==match_id)).first()
            played = (rated_by_ps is not None)
        ratings = {}
        m = DotaMatch.query.get(match_id)
        for ps in m.players_stats:
            avg_rating = DotaPlayerMatchRating.query.join(DotaPlayerMatchStats).filter(DotaPlayerMatchStats.id==ps.id)\
                .value(func.avg(DotaPlayerMatchRating.rating))
            if avg_rating:
                avg_rating = float(avg_rating) or 0.0
            ratings[ps.id] = {'allowed_rate':played and ps.id != rated_by_ps.id and not(
                    DotaPlayerMatchRating.query.filter(and_(DotaPlayerMatchRating.rated_by_steam_id==user_id, 
                    DotaPlayerMatchRating.player_match_stats_id==ps.id)).first()),
                'avg_rating':avg_rating}
        return ratings


class DotaMatch(db.Model):
    __tablename__ = 'match'

    id = Column(BigInteger, primary_key=True)
    season_id = Column(Integer, ForeignKey('season.id', onupdate="CASCADE", ondelete="CASCADE"),
                                                                                nullable=False)
    players_stats = relationship('DotaPlayerMatchStats', cascade="all,delete", backref="match", 
         order_by=DotaPlayerMatchStats.player_slot)
    radiant_win = Column(Boolean)
    duration = Column(Integer)
    game_mode = Column(Integer)
    start_time = Column(Integer)

    game_modes_dict = {1:'All Pick', 2:'Captains Mode', 3:'Random Draft', 4:'Single Draft',
        5:'All Random', 8:'Reverse Captain Mode', 16:'Captains Draft', 22:'Ranked All Pick'}

    def __repr__(self):
        return "%s" % self.id

    def to_dict(self, extensive=True):
        d = {
            'id': self.id,
            'season_number': self.season.number,
            'start_time': self.start_time,
            'game_mode': self.game_mode,
            'duration': self.duration,
            'radiant_win': self.radiant_win,
            'players_stats': [ps.to_dict(extensive) for ps in self.players_stats]
        }
        return d

    @staticmethod
    def is_exists(id):
        return bool(DotaMatch.query.get(id))

    def is_played(self, steam_id):
        return steam_id in (ps.season_stats.steam_id for ps in self.players_stats)

    def game_mode_string(self):
        return self.game_modes_dict.get(self.game_mode, 'unknown')

    def winner_string(self):
        return 'Radiant' if self.radiant_win else 'Dire'

    def duration_string(self):
        return "{}:{}".format(self.duration//60, self.duration%60)

    @staticmethod
    def get_batch(amount, offset):
        q = DotaMatch.query.order_by(desc(DotaMatch.start_time)).limit(amount).offset(amount*offset)
        return q

    @staticmethod
    def create_from_replay_fs(replay_fs):
        output = None
        fd, path = tempfile.mkstemp()
        try:
            with os.fdopen(fd, 'wb') as tmp:
                tmp.write(replay_fs.read())
            process = Popen([os.path.join(os.getcwd(), "dem2json/dem2json"), path], stdout=PIPE)
            (output, err) = process.communicate()
            exit_code = process.wait()
        finally:
            os.remove(path)
        json_data = json.loads(output.decode("utf-8"))
        DotaMatch.create_from_dict(json_data['result'])

    @staticmethod
    def create_from_dict(steamdata):
        from gleague.models.dota.player import Player
        from gleague.models.dota.season import DotaSeason

        base_pts_diff = current_app.config.get('MATCH_BASE_PTS_DIFF', 20)
        
        m = DotaMatch()
        m.season_id = DotaSeason.current().id
        m.id = steamdata['match_id']
        m.radiant_win = bool(steamdata['radiant_win'])
        m.duration = steamdata['duration']
        m.game_mode = steamdata['game_mode']
        m.start_time = steamdata['start_time']
        db.session.add(m)
        heroes = get_dota2_heroes(current_app.config['STEAM_API_KEY'])
        for i in steamdata['players']:
            player_stats = DotaPlayerMatchStats()
            account_id = '765' + str(i['account_id'] + 61197960265728)
            player = Player.get_or_create(account_id)
            if player is None:
                db.session.rollback()
                return None
            db.session.flush()
            season_stats = DotaSeasonStats.get_or_create(account_id, m.season_id)
            player_stats.season_stats_id = season_stats.id
            player_stats.kills = i['kills']
            # player_stats.hero_healing = i['hero_healing']
            player_stats.assists = i['assists']
            player_stats.level = i['level']
            player_stats.pts_diff = 20
            player_stats.deaths = i['deaths']
            player_stats.hero_damage = i['hero_damage']
            player_stats.last_hits = i['last_hits']
            player_stats.player_slot = i['player_slot']
            player_stats.denies = i['denies']
            player_stats.tower_damage = i['tower_damage']
            player_stats.damage_taken = i['damage_taken']
            player_stats.xp_per_min = i['xp_per_min']
            player_stats.gold_per_min = i['gold_per_min']
            player_stats.hero = heroes[i['hero_id']].replace('npc_dota_hero_', '')
            player_stats.match_id = m.id
            db.session.add(player_stats)
            db.session.add(season_stats)
            player_stats.old_pts = season_stats.pts
        pts = {'radiant': 0, 'dire':0}
        for i in range(len(m.players_stats)):
            j = m.players_stats[i]
            if j.player_slot < 5:
                pts['radiant'] += j.season_stats.pts
            else: pts['dire'] += j.season_stats.pts
        pts_diff = abs(pts['radiant'] - pts['dire'])/20
        if pts_diff > base_pts_diff-5: pts_diff = base_pts_diff-5
        # VERY OLD CODE; NEED REWORK
        if pts['radiant'] > pts['dire']:
            if m.radiant_win:
                for i in range(len(m.players_stats)):
                    j = m.players_stats[i]
                    if j.player_slot < 5:
                        j.pts_diff = int(base_pts_diff - pts_diff)
                    else:
                        j.pts_diff = -int(base_pts_diff - pts_diff)
                    j.season_stats.pts += j.pts_diff
            else:
                for i in range(len(m.players_stats)):
                    j = m.players_stats[i]
                    if j.player_slot < 5:
                        j.pts_diff = -int(base_pts_diff + pts_diff)
                    else:
                        j.pts_diff = int(base_pts_diff + pts_diff)
                    j.season_stats.pts += j.pts_diff
        else:
            if m.radiant_win:
                for i in range(len(m.players_stats)):
                    j = m.players_stats[i]
                    if j.player_slot < 5:
                        j.pts_diff = int(base_pts_diff + pts_diff)
                    else: 
                        j.pts_diff = -int(base_pts_diff + pts_diff)
                    j.season_stats.pts += j.pts_diff
            else:
                for i in range(len(m.players_stats)):
                    j = m.players_stats[i]
                    if j.player_slot < 5:
                        j.pts_diff = -int(base_pts_diff - pts_diff)
                    else: 
                        j.pts_diff = int(base_pts_diff - pts_diff)
                    j.season_stats.pts += j.pts_diff
        for i in range(len(m.players_stats)):
            stats = m.players_stats[i]
            season_stats = stats.season_stats
            if stats.pts_diff > 0:
                season_stats.wins += 1
                if season_stats.streak > 0:
                    season_stats.streak += 1
                else:
                    season_stats.streak = 1
                if season_stats.streak > season_stats.longest_winstreak:
                    season_stats.longest_winstreak = season_stats.streak
            else:
                if season_stats.streak > 0:
                    season_stats.streak = - 1
                else:
                    season_stats.streak -= 1
                season_stats.losses += 1
                if season_stats.streak < -(season_stats.longest_losestreak):
                    season_stats.longest_losestreak = -(season_stats.streak)
        db.session.add(m)
        db.session.flush()
        return m
