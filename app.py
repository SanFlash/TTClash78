from flask import Flask, render_template, request, jsonify
# removed websocket dependency
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json
import random
from sqlalchemy import func, text
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Production configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'table-tennis-tournament-secret')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Database configuration - Always use SQLite for simplicity
if os.environ.get('RENDER'):
    # On Render, try persistent disk first; if not writable, use /tmp
    db_dir = '/var/lib/tournament'
    db_path = os.path.join(db_dir, 'tournament.db')
    
    # Test if we can write to the persistent disk
    try:
        test_file = os.path.join(db_dir, '.write_test')
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        print(f"Using persistent disk: {db_path}")
    except (PermissionError, FileNotFoundError, IOError):
        # Fall back to /tmp which is always writable on Render
        db_path = '/tmp/tournament.db'
        print(f"Persistent disk not writable, using temp directory: {db_path}")
else:
    # Use local file in development
    db_path = 'tournament.db'

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'

db = SQLAlchemy(app)
# SocketIO removed, using plain Flask


# Database initialization function
def init_db():
    """Initialize database tables if they don't exist"""
    try:
        with app.app_context():
            db.create_all()
    except Exception as e:
        print(f'Error initializing database: {e}')

# Schema migration helper for SQLite
def migrate_schema():
    with app.app_context():
        engine = db.get_engine()
        if 'sqlite' in str(engine.url):
            with engine.connect() as conn:
                player_cols = [c[1] for c in conn.execute(text("PRAGMA table_info(player)"))]
                if 'avatar_url' not in player_cols:
                    try:
                        conn.execute(text("ALTER TABLE player ADD COLUMN avatar_url TEXT DEFAULT ''"))
                        print('Added player.avatar_url column')
                    except Exception as e:
                        print('Could not add avatar_url:', e)

                match_cols = [c[1] for c in conn.execute(text("PRAGMA table_info(match)"))]
                if 'tournament_type' not in match_cols:
                    try:
                        conn.execute(text("ALTER TABLE match ADD COLUMN tournament_type TEXT DEFAULT 'league'"))
                        print('Added match.tournament_type column')
                    except Exception as e:
                        print('Could not add tournament_type:', e)
                if 'max_sets' not in match_cols:
                    try:
                        conn.execute(text("ALTER TABLE match ADD COLUMN max_sets INTEGER DEFAULT 5"))
                        print('Added match.max_sets column')
                    except Exception as e:
                        print('Could not add max_sets:', e)

# Initialize database before first request
@app.before_request
def before_request():
    """Ensure database is initialized"""
    try:
        # Test if tables exist by doing a simple query
        Player.query.first()
    except Exception:
        # If tables don't exist, create them
        init_db()

# Database Models
class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    avatar_url = db.Column(db.String(255), nullable=True, default='')
    group_id = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player1_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=False)
    player2_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=False)
    group_id = db.Column(db.String(10), nullable=False)
    stage = db.Column(db.String(30), default='league')  # league, knockout, league-knockout etc
    tournament_type = db.Column(db.String(30), default='league')
    max_sets = db.Column(db.Integer, default=5)
    round_number = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='pending')  # pending, completed
    winner_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

class MatchScore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey('match.id'), nullable=False)
    set_number = db.Column(db.Integer, nullable=False)
    player1_score = db.Column(db.Integer, nullable=False)
    player2_score = db.Column(db.Integer, nullable=False)

class Standing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=False)
    group_id = db.Column(db.String(10), nullable=False)
    matches_played = db.Column(db.Integer, default=0)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    sets_won = db.Column(db.Integer, default=0)
    sets_lost = db.Column(db.Integer, default=0)
    points_for = db.Column(db.Integer, default=0)
    points_against = db.Column(db.Integer, default=0)
    ranking_points = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Fixture Generation Service
class FixtureService:
    @staticmethod
    def generate_round_robin_fixtures(players, group_id, max_sets=5, tournament_type='league'):
        """Generate round-robin fixtures using circle method"""
        if len(players) < 2:
            return []

        # Shuffle players for randomness
        players_copy = players.copy()
        random.shuffle(players_copy)

        fixtures = []
        n = len(players_copy)

        # If odd number, add a dummy player
        if n % 2 == 1:
            players_copy.append(None)
            n += 1

        # Generate rounds
        for round_num in range(n - 1):
            round_fixtures = []
            for i in range(n // 2):
                player1 = players_copy[i]
                player2 = players_copy[n - 1 - i]

                if player1 is not None and player2 is not None:
                    match = Match(
                        player1_id=player1.id,
                        player2_id=player2.id,
                        group_id=group_id,
                        round_number=round_num + 1,
                        stage='league',
                        tournament_type=tournament_type,
                        max_sets=max_sets
                    )
                    round_fixtures.append(match)

            # Rotate players (except first)
            players_copy = [players_copy[0]] + [players_copy[-1]] + players_copy[1:-1]
            fixtures.extend(round_fixtures)

        return fixtures

    @staticmethod
    def generate_knockout_fixtures(players, group_id, max_sets=5, tournament_type='knockout'):
        """Generate single-elimination knockout bracket"""
        players_copy = players.copy()
        random.shuffle(players_copy)

        fixtures = []
        round_number = 1

        while len(players_copy) > 1:
            next_round = []
            for i in range(0, len(players_copy), 2):
                if i + 1 < len(players_copy):
                    player1 = players_copy[i]
                    player2 = players_copy[i+1]
                    match = Match(
                        player1_id=player1.id,
                        player2_id=player2.id,
                        group_id=group_id,
                        round_number=round_number,
                        stage='knockout',
                        tournament_type=tournament_type,
                        max_sets=max_sets
                    )
                    fixtures.append(match)
                    # placeholder for advancing winners
                    next_round.append(None)
                else:
                    # bye, advance player
                    next_round.append(players_copy[i])
            players_copy = next_round
            round_number += 1

        return fixtures

    @staticmethod
    def generate_league_knockout_fixtures(players, group_id, max_sets=5, advance_top=2):
        league_fixtures = FixtureService.generate_round_robin_fixtures(players, group_id, max_sets, 'league-cum-knockout')
        # knockout stage will be created dynamically after standings are computed, so just return league fixtures.
        return league_fixtures

    @staticmethod
    def generate_knockout_knockout_fixtures(players, group_id, max_sets=5):
        # For simplicity build one knockout stage; second stage can be bye winners
        return FixtureService.generate_knockout_fixtures(players, group_id, max_sets, 'knockout-cum-knockout')

    @staticmethod
    def generate_friendly_fixtures(players, group_id, max_sets=3):
        return FixtureService.generate_round_robin_fixtures(players, group_id, max_sets, 'friendly')

# Ranking Service
class RankingService:
    @staticmethod
    def calculate_standings(player_id, group_id):
        """Calculate standings for a player"""
        player = db.session.get(Player, player_id)
        if not player:
            return None
        
        # Get all matches for this player
        matches = Match.query.filter(
            ((Match.player1_id == player_id) | (Match.player2_id == player_id)) &
            (Match.group_id == group_id) &
            (Match.status == 'completed')
        ).all()
        
        stats = {
            'matches_played': 0,
            'wins': 0,
            'losses': 0,
            'sets_won': 0,
            'sets_lost': 0,
            'points_for': 0,
            'points_against': 0,
            'ranking_points': 0
        }
        
        for match in matches:
            stats['matches_played'] += 1
            
            # Get match scores
            scores = MatchScore.query.filter_by(match_id=match.id).all()
            
            player_sets_won = 0
            opponent_sets_won = 0
            
            for score in scores:
                if match.player1_id == player_id:
                    if score.player1_score > score.player2_score:
                        player_sets_won += 1
                    else:
                        opponent_sets_won += 1
                    stats['points_for'] += score.player1_score
                    stats['points_against'] += score.player2_score
                else:
                    if score.player2_score > score.player1_score:
                        player_sets_won += 1
                    else:
                        opponent_sets_won += 1
                    stats['points_for'] += score.player2_score
                    stats['points_against'] += score.player1_score
            
            stats['sets_won'] += player_sets_won
            stats['sets_lost'] += opponent_sets_won
            
            # Determine win/loss
            if match.winner_id == player_id:
                stats['wins'] += 1
                stats['ranking_points'] += 2  # Win = 2 points
            else:
                stats['losses'] += 1
                # Loss = 0 points (no change)
        
        return stats
    
    @staticmethod
    def update_all_standings(group_id):
        """Update standings for all players in a group"""
        players = Player.query.filter_by(group_id=group_id).all()
        
        for player in players:
            stats = RankingService.calculate_standings(player.id, group_id)
            
            if stats:
                standing = Standing.query.filter_by(player_id=player.id, group_id=group_id).first()
                if not standing:
                    standing = Standing(player_id=player.id, group_id=group_id)
                    db.session.add(standing)
                
                # Update standing with new stats
                standing.matches_played = stats['matches_played']
                standing.wins = stats['wins']
                standing.losses = stats['losses']
                standing.sets_won = stats['sets_won']
                standing.sets_lost = stats['sets_lost']
                standing.points_for = stats['points_for']
                standing.points_against = stats['points_against']
                standing.ranking_points = stats['ranking_points']
        
        db.session.commit()

# Qualification Service
class QualificationService:
    @staticmethod
    def predict_qualification(group_id, top_n=2):
        """Predict qualification chances for players in a group"""
        players = Player.query.filter_by(group_id=group_id).all()
        standings = Standing.query.filter_by(group_id=group_id).all()
        
        # Get current standings sorted by ranking points
        standings_sorted = sorted(standings, key=lambda x: x.ranking_points, reverse=True)
        
        predictions = []
        
        for standing in standings:
            player = db.session.get(Player, standing.player_id)
            
            # Calculate remaining matches
            remaining_matches = Match.query.filter(
                ((Match.player1_id == player.id) | (Match.player2_id == player.id)) &
                (Match.group_id == group_id) &
                (Match.status == 'pending')
            ).count()
            
            # Calculate maximum possible points
            max_possible_points = standing.ranking_points + (remaining_matches * 2)
            
            # Check if currently in qualification position
            current_position = next(i for i, s in enumerate(standings_sorted) if s.player_id == player.id) + 1
            
            # Simple qualification prediction
            if current_position <= top_n:
                status = "Likely Qualified"
                probability = 85
            elif max_possible_points >= standings_sorted[top_n-1].ranking_points:
                status = "Can Still Qualify"
                probability = 60
            else:
                status = "Eliminated"
                probability = 10
            
            predictions.append({
                'player': player,
                'current_position': current_position,
                'ranking_points': standing.ranking_points,
                'remaining_matches': remaining_matches,
                'max_possible_points': max_possible_points,
                'status': status,
                'probability': probability
            })
        
        return predictions

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/fixtures')
def fixtures():
    return render_template('fixtures.html')

@app.route('/standings')
def standings():
    return render_template('standings.html')

@app.route('/match-entry')
def match_entry():
    return render_template('match_entry.html')

# API Routes
@app.route('/api/players', methods=['GET', 'POST'])
def handle_players():
    if request.method == 'POST':
        try:
            data = request.json
            if not data or 'name' not in data or 'group_id' not in data:
                return jsonify({'error': 'Missing required fields: name and group_id'}), 400
            
            name = data['name'].strip()
            group_id = data['group_id'].strip()
            avatar_url = data.get('avatar_url', '').strip()
            
            if not name:
                return jsonify({'error': 'Player name cannot be empty'}), 400
            
            if not group_id or group_id not in ['A', 'B', 'C', 'D']:
                return jsonify({'error': 'Group ID must be A, B, C, or D'}), 400
            
            player = Player(name=name, group_id=group_id, avatar_url=avatar_url)
            db.session.add(player)
            db.session.commit()
            return jsonify({'id': player.id, 'name': player.name, 'group_id': player.group_id, 'avatar_url': player.avatar_url}), 201
        except Exception as e:
            db.session.rollback()
            print(f'Error adding player: {e}')
            return jsonify({'error': f'Failed to add player: {str(e)}'}), 500
    
    try:
        players = Player.query.all()
        return jsonify([{
            'id': p.id,
            'name': p.name,
            'group_id': p.group_id,
            'avatar_url': p.avatar_url or ''
        } for p in players])
    except Exception as e:
        print(f'Error fetching players: {e}')
        return jsonify({'error': 'Failed to fetch players'}), 500

@app.route('/api/players/<int:player_id>', methods=['PUT', 'DELETE'])
def handle_player(player_id):
    player = db.session.get(Player, player_id)
    if not player:
        return jsonify({'error': 'Player not found'}), 404
    
    if request.method == 'PUT':
        data = request.json
        player.name = data.get('name', player.name)
        player.group_id = data.get('group_id', player.group_id)
        db.session.commit()
        return jsonify({'id': player.id, 'name': player.name, 'group_id': player.group_id})
    
    elif request.method == 'DELETE':
        # Delete all matches involving this player
        Match.query.filter(
            (Match.player1_id == player_id) | 
            (Match.player2_id == player_id) | 
            (Match.winner_id == player_id)
        ).delete()
        
        # Delete associated standings
        Standing.query.filter_by(player_id=player_id).delete()
        
        # Delete player
        db.session.delete(player)
        db.session.commit()
        return jsonify({'message': 'Player deleted successfully'})

@app.route('/api/generate-fixtures', methods=['POST'])
def generate_fixtures():
    data = request.json
    group_id = data.get('group_id', 'A')
    tournament_type = data.get('tournament_type', 'league')
    max_sets = int(data.get('max_sets', 5))

    if max_sets < 1:
        max_sets = 5

    # Clear ALL existing matches for this group to ensure clean slate
    Match.query.filter_by(group_id=group_id).delete()
    db.session.commit()

    # Get current players for the group
    players = Player.query.filter_by(group_id=group_id).all()

    if len(players) < 2:
        return jsonify({'error': 'Need at least 2 players to generate fixtures'}), 400

    valid_players = [p for p in players if db.session.get(Player, p.id)]

    if len(valid_players) < 2:
        return jsonify({'error': 'Need at least 2 valid players to generate fixtures'}), 400

    if tournament_type == 'league':
        fixtures = FixtureService.generate_round_robin_fixtures(valid_players, group_id, max_sets, 'league')
    elif tournament_type == 'knockout':
        fixtures = FixtureService.generate_knockout_fixtures(valid_players, group_id, max_sets, 'knockout')
    elif tournament_type == 'league-knockout':
        fixtures = FixtureService.generate_league_knockout_fixtures(valid_players, group_id, max_sets)
    elif tournament_type == 'knockout-knockout':
        fixtures = FixtureService.generate_knockout_knockout_fixtures(valid_players, group_id, max_sets)
    elif tournament_type == 'friendly':
        fixtures = FixtureService.generate_friendly_fixtures(valid_players, group_id, max_sets)
    else:
        fixtures = FixtureService.generate_round_robin_fixtures(valid_players, group_id, max_sets, tournament_type)

    for fixture in fixtures:
        db.session.add(fixture)

    db.session.commit()

    return jsonify({'message': f'Generated {len(fixtures)} fixtures for group {group_id} ({tournament_type}) with {len(valid_players)} players', 'tournament_type': tournament_type, 'max_sets': max_sets})

@app.route('/api/matches/<group_id>')
def get_matches(group_id):
    matches = Match.query.filter_by(group_id=group_id).all()
    result = []
    
    for match in matches:
        player1 = db.session.get(Player, match.player1_id)
        player2 = db.session.get(Player, match.player2_id)
        winner = db.session.get(Player, match.winner_id) if match.winner_id else None
        
        # Skip matches where players no longer exist (data integrity issue)
        if not player1 or not player2:
            continue
        
        result.append({
            'id': match.id,
            'player1': {'id': player1.id, 'name': player1.name, 'avatar_url': player1.avatar_url},
            'player2': {'id': player2.id, 'name': player2.name, 'avatar_url': player2.avatar_url},
            'round_number': match.round_number,
            'status': match.status,
            'winner': {'id': winner.id, 'name': winner.name} if winner else None,
            'stage': match.stage,
            'tournament_type': match.tournament_type,
            'max_sets': match.max_sets
        })

    
    return jsonify(result)

@app.route('/api/submit-result', methods=['POST'])
def submit_result():
    data = request.json
    match_id = data['match_id']
    scores = data['scores']  # Array of {set_number, player1_score, player2_score}
    
    match = Match.query.get(match_id)
    if not match:
        return jsonify({'error': 'Match not found'}), 404
    
    # Clear existing scores
    MatchScore.query.filter_by(match_id=match_id).delete()
    
    # Add new scores
    player1_sets_won = 0
    player2_sets_won = 0
    
    for score_data in scores:
        score = MatchScore(
            match_id=match_id,
            set_number=score_data['set_number'],
            player1_score=score_data['player1_score'],
            player2_score=score_data['player2_score']
        )
        db.session.add(score)
        
        # Count sets won
        if score_data['player1_score'] > score_data['player2_score']:
            player1_sets_won += 1
        else:
            player2_sets_won += 1
    
    # Determine winner based on sets won first, then total points tiebreaker
    player1_points = sum([s.player1_score for s in MatchScore.query.filter_by(match_id=match_id).all()])
    player2_points = sum([s.player2_score for s in MatchScore.query.filter_by(match_id=match_id).all()])

    if player1_sets_won > player2_sets_won:
        match.winner_id = match.player1_id
    elif player2_sets_won > player1_sets_won:
        match.winner_id = match.player2_id
    else:
        # tie on set count, use total points
        if player1_points > player2_points:
            match.winner_id = match.player1_id
        elif player2_points > player1_points:
            match.winner_id = match.player2_id
        else:
            # perfect tie, fallback to set wins by score differential
            match.winner_id = match.player1_id if player1_points >= player2_points else match.player2_id

    match.status = 'completed'
    match.completed_at = datetime.utcnow()
    
    db.session.commit()
    
    # Update standings
    RankingService.update_all_standings(match.group_id)
    
    # Broadcast update to all connected clients
    # previously emitted websocket event; now do nothing or handle client polling
    {
        'match_id': match_id,
        'group_id': match.group_id,
        'winner_id': match.winner_id
    }
    
    return jsonify({'message': 'Result submitted successfully'})

@app.route('/api/standings/<group_id>')
def get_standings(group_id):
    standings = Standing.query.filter_by(group_id=group_id).all()
    result = []
    
    # Sort by ranking points (descending)
    standings_sorted = sorted(standings, key=lambda x: x.ranking_points, reverse=True)
    
    for i, standing in enumerate(standings_sorted):
        player = db.session.get(Player, standing.player_id)
        
        # Calculate set difference and point difference
        set_diff = standing.sets_won - standing.sets_lost
        point_diff = standing.points_for - standing.points_against
        
        result.append({
            'rank': i + 1,
            'player': {'id': player.id, 'name': player.name},
            'matches_played': standing.matches_played,
            'wins': standing.wins,
            'losses': standing.losses,
            'sets_won': standing.sets_won,
            'sets_lost': standing.sets_lost,
            'set_difference': set_diff,
            'points_for': standing.points_for,
            'points_against': standing.points_against,
            'point_difference': point_diff,
            'ranking_points': standing.ranking_points
        })
    
    return jsonify(result)

@app.route('/api/qualification/<group_id>')
def get_qualification(group_id):
    predictions = QualificationService.predict_qualification(group_id)
    result = []
    
    for pred in predictions:
        result.append({
            'player': {'id': pred['player'].id, 'name': pred['player'].name},
            'current_position': pred['current_position'],
            'ranking_points': pred['ranking_points'],
            'remaining_matches': pred['remaining_matches'],
            'max_possible_points': pred['max_possible_points'],
            'status': pred['status'],
            'probability': pred['probability']
        })
    
    return jsonify(result)

# WebSocket events
# websocket handlers removed

# websocket handlers removed

if __name__ == '__main__':
    with app.app_context():
        init_db()
        migrate_schema()
    
    # Production deployment configuration
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    
    app.run(debug=debug, host='0.0.0.0', port=port)