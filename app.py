import dash
from dash import dcc, html, Input, Output, State, dash_table, no_update
import dash_bootstrap_components as dbc
from dash_bootstrap_templates import load_figure_template
import pandas as pd
from datetime import datetime
import os
import plotly.express as px
import plotly.graph_objects as go
import json
from functools import reduce
import operator
import sqlalchemy as sa
from sqlalchemy import inspect, text, JSON
import numpy as np

# Initialize the app with Bootstrap theme
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.CYBORG])
load_figure_template("cyborg")  # Dark theme for charts
app.title = "Football Bet Tracker Pro"

# Database setup
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set")

engine = sa.create_engine(DATABASE_URL.replace('postgres://', 'postgresql+psycopg2://'))

# Create tables if they don't exist
with engine.connect() as conn:
    inspector = inspect(engine)
    
    if not inspector.has_table('bets'):
        conn.execute(text("""
            CREATE TABLE bets (
                date TIMESTAMP,
                match TEXT,
                prediction TEXT,
                bet_amount FLOAT,
                odds FLOAT,
                outcome TEXT,
                result_amount FLOAT,
                profit_loss FLOAT,
                wager_type TEXT,
                selections JSONB,
                slip_no INTEGER,
                status TEXT
            )
        """))
    
    if not inspector.has_table('settings'):
        conn.execute(text("""
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value FLOAT
            )
        """))
        # Insert default settings
        defaults = [
            {'key': 'initial_bankroll', 'value': 1000.0},
            {'key': 'max_bet_percent', 'value': 5.0}
        ]
        for default in defaults:
            conn.execute(text("INSERT INTO settings (key, value) VALUES (:key, :value)"), default)
        conn.commit()

# Load data
def load_data():
    with engine.connect() as conn:
        df = pd.read_sql('SELECT * FROM bets', conn)
        if not df.empty:
            df = df.astype({
                'bet_amount': 'float64',
                'odds': 'float64',
                'result_amount': 'float64',
                'profit_loss': 'float64'
            })
            df['date'] = pd.to_datetime(df['date'])
            df['selections'] = df['selections'].apply(lambda x: x if isinstance(x, list) else [])
            if 'wager_type' not in df.columns:
                df['wager_type'] = 'Single'
            if 'slip_no' not in df.columns:
                df['slip_no'] = None
            if 'status' not in df.columns:
                df['status'] = np.where(df['outcome'].isna(), 'Pending', np.where(df['profit_loss'] > 0, 'Win', 'Loss'))
            df = renumber_slips(df)
        else:
            df = pd.DataFrame(columns=[
                'date', 'match', 'prediction', 'bet_amount', 'odds', 
                'outcome', 'result_amount', 'profit_loss', 'wager_type', 'selections', 'slip_no', 'status'
            ])
            df = df.astype({
                'bet_amount': 'float64',
                'odds': 'float64',
                'result_amount': 'float64',
                'profit_loss': 'float64'
            })
    return df

def renumber_slips(df):
    if not df.empty:
        df = df.sort_values('date').reset_index(drop=True)
        df['slip_no'] = df.index + 1
    return df

# Save data
def save_data(df):
    df_save = df.copy()
    df_save['selections'] = df_save['selections'].apply(lambda x: x if isinstance(x, list) else [])
    with engine.connect() as conn:
        df_save.to_sql('bets', conn, if_exists='replace', index=False, dtype={'selections': JSON})
        conn.commit()

# Load settings
def load_settings():
    with engine.connect() as conn:
        settings_df = pd.read_sql('SELECT * FROM settings', conn)
        if settings_df.empty:
            settings = {'initial_bankroll': 1000.0, 'max_bet_percent': 5.0}
            save_settings(settings)
        else:
            settings = dict(zip(settings_df['key'], settings_df['value']))
    return settings

# Save settings
def save_settings(settings):
    with engine.connect() as conn:
        for k, v in settings.items():
            conn.execute(text("INSERT INTO settings (key, value) VALUES (:key, :value) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"), {'key': k, 'value': v})
        conn.commit()

# Get display data for tables
def get_display_data(df_raw, currency):
    symbols = {'NLE': 'Le', 'USD': '$', 'EUR': '€'}
    symbol = symbols.get(currency, '$')
    df = df_raw.copy()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d %H:%M')
        df['selections'] = df['selections'].apply(
            lambda x: ', '.join(f"{s.get('match', '')} {s.get('prediction', '')} @ {round(s.get('odds', 1.0), 2):.2f}" for s in x) 
            if isinstance(x, list) and len(x) > 0 else ''
        )
        df['slip_no'] = df['slip_no'].apply(lambda x: f"Slip No: {x}" if pd.notna(x) else '')
        df['bet_amount'] = df['bet_amount'].apply(lambda x: f"{symbol}{round(float(x), 2):.2f}" if pd.notna(x) else "")
        df['odds'] = df['odds'].apply(lambda x: f"{round(float(x), 2):.2f}" if pd.notna(x) else "")
        df['result_amount'] = df['result_amount'].apply(lambda x: f"{symbol}{round(float(x), 2):.2f}" if pd.notna(x) else "")
        df['profit_loss'] = df['profit_loss'].apply(lambda x: f"{symbol}{round(float(x), 2):+.2f}" if pd.notna(x) else "")
        df['outcome'] = df['outcome'].fillna('Pending')
    return df.to_dict('records')

# Initial data
df = load_data()
settings = load_settings()

# Initial display data
initial_table_data = get_display_data(df, 'USD')

# Function to categorize bet type
def categorize_bet_type(row):
    return row['wager_type']

# Function to compute streaks
def get_streaks(df):
    if df.empty:
        return 0, 0
    df_sorted = df.sort_values('date')
    settled = df_sorted[df_sorted['outcome'].notna()].copy()
    if settled.empty:
        return 0, 0
    mask_single_win = (settled['wager_type'] == 'Single') & (settled['outcome'] == settled['prediction'])
    mask_acc_win = (settled['wager_type'] == 'Accumulator') & (settled['outcome'] == 'Win')
    settled['result'] = (mask_single_win | mask_acc_win).astype(int)
    settled['streak_group'] = (settled['result'] != settled['result'].shift()).cumsum()
    streaks = settled.groupby('streak_group').agg({
        'result': ['first', 'count']
    }).droplevel(0, axis=1)
    streaks.columns = ['is_win', 'length']
    max_win = streaks[streaks['is_win'] == 1]['length'].max() if not streaks[streaks['is_win'] == 1].empty else 0
    max_loss = streaks[streaks['is_win'] == 0]['length'].max() if not streaks[streaks['is_win'] == 0].empty else 0
    return max_win, max_loss

# Layout components
header = dbc.NavbarSimple(
    brand="Football Bet Tracker Pro",
    brand_href="#",
    color="primary",
    dark=True,
)

currency_dropdown = dcc.Dropdown(
    id='currency-dropdown',
    options=[
        {'label': 'Leones (NLE)', 'value': 'NLE'},
        {'label': 'Dollars (USD)', 'value': 'USD'},
        {'label': 'Euros (EUR)', 'value': 'EUR'}
    ],
    value='USD',
    style={'width': '120px', 'margin-left': 'auto'}
)

header_with_currency = dbc.Navbar(
    [
        html.Div([
            html.H2("Football Bet Tracker Pro", className="navbar-brand"),
            dbc.NavbarToggler(id="navbar-toggler", className="ms-auto"),
        ], className="container-fluid"),
        dbc.Collapse(currency_dropdown, id="navbar-collapse", is_open=True, className="ms-auto"),
    ],
    color="primary",
    dark=True,
)

# Bankroll Settings Card
bankroll_settings_card = dbc.Card([
    dbc.CardHeader("Bankroll Management Settings", className="h5"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col(dbc.Input(id='initial-bankroll-input', placeholder='Initial Bankroll', type='number', value=settings['initial_bankroll']), width=6),
            dbc.Col(dbc.Input(id='max-bet-percent-input', placeholder='Max Bet % (e.g., 5)', type='number', value=settings['max_bet_percent'], step=0.1), width=6),
        ], className="g-2 mb-3"),
        dbc.Button('Update Settings', id='update-settings-btn', n_clicks=0, color="info"),
        dbc.Alert(id='settings-feedback', color="success", dismissable=True, is_open=False, className="d-none mt-2"),
    ])
], className="mb-4")

add_bet_card = dbc.Card([
    dbc.CardHeader("Add a New Bet", className="h5"),
    dbc.CardBody([
        dbc.Select(
            id='bet-type-select',
            options=[
                {'label': 'Single Bet', 'value': 'Single'},
                {'label': 'Accumulator', 'value': 'Accumulator'}
            ],
            value='Single'
        ),
        dbc.Collapse(
            id='single-collapse',
            is_open=True,
            children=[
                dbc.Row([
                    dbc.Col(dbc.Input(id='match-input', placeholder='e.g., TeamA vs TeamB', type='text'), width=12),
                    dbc.Col(dbc.Input(id='prediction-input', placeholder='e.g., TeamA or Draw', type='text'), width=6),
                ], className="g-2 mb-3")
            ]
        ),
        dbc.Collapse(
            id='acc-collapse',
            is_open=False,
            children=[
                dbc.Textarea(
                    id='selections-input',
                    placeholder='Enter one selection per line:\nMatch Prediction Odds\n e.g.\nArsenal vs Tottenham Arsenal 1.8\nMan Utd vs Liverpool Draw 2.5',
                    rows=4
                ),
            ],
            className="mb-3"
        ),
        dbc.Row([
            dbc.Col(dbc.Input(id='bet-amount-input', placeholder='10', type='number', value=10), width=6),
            dbc.Col(dbc.Input(id='odds-input', placeholder='2.0', type='number', value=2.0), width=6),
        ], className="g-2 mb-3"),
        dbc.Button('Add Bet', id='add-bet-btn', n_clicks=0, color="success", className="me-2"),
        dbc.Alert(id='add-feedback', color="success", dismissable=True, is_open=False, className="d-none"),
        dbc.Alert(id='risky-wager-alert', color="warning", dismissable=True, is_open=False, className="d-none mt-2"),
    ])
], className="mb-4")

columns = [
    {"name": i.replace('_', ' ').title() if i not in ['date', 'selections', 'profit_loss', 'result_amount', 'slip_no'] else 'Date' if i=='date' else 'Selections' if i=='selections' else 'Profit/Loss' if i=='profit_loss' else 'Result Amount' if i=='result_amount' else 'Slip No' if i=='slip_no' else i.title(), "id": i} 
    for i in df.columns if i not in ['status']
]
columns.append({"name": "Status", "id": "status"})

update_outcome_card = dbc.Card([
    dbc.CardHeader("Manage Bets (Select a row to Update Outcome, Edit, or Delete)", className="h5"),
    dbc.CardBody([
        dash_table.DataTable(
            id='bets-table-update',
            columns=columns,
            data=initial_table_data,
            page_size=5,
            row_selectable='single',
            style_cell={'textAlign': 'left', 'padding': '10px', 'color': 'white', 'whiteSpace': 'normal', 'width': 'auto'},
            style_header={'backgroundColor': 'rgb(230, 230, 230)', 'fontWeight': 'bold', 'color': 'black', 'width': 'auto'},
            style_table={'overflowX': 'auto', 'width': '100%'},
            style_cell_conditional=[
                {'if': {'column_id': 'date'}, 'width': '120px'},
                {'if': {'column_id': 'match'}, 'width': '150px'},
                {'if': {'column_id': 'prediction'}, 'width': '100px'},
                {'if': {'column_id': 'bet_amount'}, 'width': '100px'},
                {'if': {'column_id': 'odds'}, 'width': '80px'},
                {'if': {'column_id': 'outcome'}, 'width': '100px'},
                {'if': {'column_id': 'result_amount'}, 'width': '120px'},
                {'if': {'column_id': 'profit_loss'}, 'width': '120px'},
                {'if': {'column_id': 'wager_type'}, 'width': '100px'},
                {'if': {'column_id': 'selections'}, 'width': '200px', 'overflow': 'auto'},
                {'if': {'column_id': 'slip_no'}, 'width': '100px'},
                {
                    'if': {'column_id': 'status'},
                    'display': 'none'
                }
            ],
            style_header_conditional=[
                {
                    'if': {'column_id': 'status'},
                    'display': 'none'
                }
            ],
            style_data_conditional=[
                {
                    'if': {'filter_query': '{status} = Win'},
                    'color': '#28a745',
                    'fontWeight': 'bold',
                    'backgroundColor': 'rgba(40, 167, 69, 0.1)'
                },
                {
                    'if': {'filter_query': '{status} = Loss'},
                    'color': '#dc3545',
                    'fontWeight': 'bold',
                    'backgroundColor': 'rgba(220, 53, 69, 0.1)'
                },
                {
                    'if': {'filter_query': '{status} = Pending'},
                    'color': 'white',
                    'fontWeight': 'bold',
                    'backgroundColor': 'transparent'
                }
            ]
        ),
        dbc.Row([
            dbc.Col(dbc.Input(id='outcome-input', placeholder="Win/Loss/Pending or specific outcome (e.g., TeamA for Single)", type='text'), width=6),
            dbc.Col([
                dbc.Button('Update Outcome', id='update-outcome-btn', n_clicks=0, color="warning", className="me-1"),
                dbc.Button('Edit Bet', id='edit-bet-btn', n_clicks=0, color="primary", className="me-1"),
                dbc.Button('Delete Bet', id='delete-bet-btn', n_clicks=0, color="danger")
            ], width=6),
        ], className="g-2 mt-3"),
        dbc.Alert(id='action-feedback', color="info", dismissable=True, is_open=False, className="d-none"),
    ])
], className="mb-4")

bets_table_card = dbc.Card([
    dbc.CardHeader("All Bets", className="h5"),
    dbc.CardBody([
        dash_table.DataTable(
            id='bets-table',
            columns=columns,
            data=initial_table_data,
            page_size=5,
            row_selectable=None,
            style_cell={'textAlign': 'left', 'padding': '10px', 'color': 'white', 'whiteSpace': 'normal', 'width': 'auto'},
            style_header={'backgroundColor': 'rgb(230, 230, 230)', 'fontWeight': 'bold', 'color': 'black', 'width': 'auto'},
            style_table={'overflowX': 'auto', 'width': '100%'},
            style_cell_conditional=[
                {'if': {'column_id': 'date'}, 'width': '120px'},
                {'if': {'column_id': 'match'}, 'width': '150px'},
                {'if': {'column_id': 'prediction'}, 'width': '100px'},
                {'if': {'column_id': 'bet_amount'}, 'width': '100px'},
                {'if': {'column_id': 'odds'}, 'width': '80px'},
                {'if': {'column_id': 'outcome'}, 'width': '100px'},
                {'if': {'column_id': 'result_amount'}, 'width': '120px'},
                {'if': {'column_id': 'profit_loss'}, 'width': '120px'},
                {'if': {'column_id': 'wager_type'}, 'width': '100px'},
                {'if': {'column_id': 'selections'}, 'width': '200px', 'overflow': 'auto'},
                {'if': {'column_id': 'slip_no'}, 'width': '100px'},
                {
                    'if': {'column_id': 'status'},
                    'display': 'none'
                }
            ],
            style_header_conditional=[
                {
                    'if': {'column_id': 'status'},
                    'display': 'none'
                }
            ],
            style_data_conditional=[
                {
                    'if': {'filter_query': '{status} = Win'},
                    'color': '#28a745',
                    'fontWeight': 'bold',
                    'backgroundColor': 'rgba(40, 167, 69, 0.1)'
                },
                {
                    'if': {'filter_query': '{status} = Loss'},
                    'color': '#dc3545',
                    'fontWeight': 'bold',
                    'backgroundColor': 'rgba(220, 53, 69, 0.1)'
                },
                {
                    'if': {'filter_query': '{status} = Pending'},
                    'color': 'white',
                    'fontWeight': 'bold',
                    'backgroundColor': 'transparent'
                }
            ]
        )
    ])
], className="mb-4")

summary_card = dbc.Card([
    dbc.CardHeader("Advanced Betting Summary", className="h5"),
    dbc.CardBody([
        dbc.Row(id='summary-stats', children=[]),
    ])
], className="mb-4")

# Charts row
charts_row = dbc.Row([
    dbc.Col([
        dbc.Card([
            dbc.CardHeader("Profit/Loss per Bet", className="h5"),
            dbc.CardBody([
                dcc.Graph(id='profit-bar-chart')
            ])
        ], className="mb-4")
    ], width=6),
    dbc.Col([
        dbc.Card([
            dbc.CardHeader("Cumulative P/L Over Time", className="h5"),
            dbc.CardBody([
                dcc.Graph(id='cumulative-line-chart')
            ])
        ], className="mb-4")
    ], width=6),
], className="g-4 mb-4")

# Additional analytics row
analytics_row = dbc.Row([
    dbc.Col([
        dbc.Card([
            dbc.CardHeader("Profit by Bet Type", className="h5"),
            dbc.CardBody([
                dcc.Graph(id='bet-category-bar-chart')
            ])
        ])
    ], width=12)
], className="g-4")

# Time-based analytics row
time_charts_row = dbc.Row([
    dbc.Col([
        dbc.Card([
            dbc.CardHeader("Yearly P/L", className="h5"),
            dbc.CardBody([
                dcc.Graph(id='yearly-chart', style={'height': '250px'})
            ])
        ])
    ], width=3),
    dbc.Col([
        dbc.Card([
            dbc.CardHeader("Monthly P/L", className="h5"),
            dbc.CardBody([
                dcc.Graph(id='monthly-chart', style={'height': '250px'})
            ])
        ])
    ], width=3),
    dbc.Col([
        dbc.Card([
            dbc.CardHeader("Weekly P/L", className="h5"),
            dbc.CardBody([
                dcc.Graph(id='weekly-chart', style={'height': '250px'})
            ])
        ])
    ], width=3),
    dbc.Col([
        dbc.Card([
            dbc.CardHeader("P/L by Day of Week", className="h5"),
            dbc.CardBody([
                dcc.Graph(id='dow-chart', style={'height': '250px'})
            ])
        ])
    ], width=3),
], className="g-4")

# Calendar heatmap row
calendar_row = dbc.Row([
    dbc.Col([
        dbc.Card([
            dbc.CardHeader("Betting Performance Calendar Heatmap", className="h5"),
            dbc.CardBody([
                dcc.Graph(id='calendar-heatmap', style={'height': '300px'})
            ])
        ], className="mb-4")
    ], width=12)
], className="g-4")

# Edit Modal
edit_modal = dbc.Modal([
    dbc.ModalHeader(dbc.ModalTitle("Edit Bet")),
    dbc.ModalBody([
        dbc.Select(
            id='edit-wager-type',
            options=[
                {'label': 'Single Bet', 'value': 'Single'},
                {'label': 'Accumulator', 'value': 'Accumulator'}
            ],
            value='Single'
        ),
        dbc.Collapse(
            id='edit-single-collapse',
            is_open=True,
            children=[
                dbc.Row([
                    dbc.Col(dbc.Input(id='edit-match', placeholder='Match', type='text'), width=12),
                    dbc.Col(dbc.Input(id='edit-prediction', placeholder='Prediction', type='text'), width=6),
                ], className="g-2")
            ]
        ),
        dbc.Collapse(
            id='edit-acc-collapse',
            is_open=False,
            children=[
                dbc.Textarea(
                    id='edit-selections-input',
                    placeholder='Enter one selection per line:\nMatch Prediction Odds',
                    rows=4
                ),
            ],
            className="mb-3"
        ),
        dbc.Row([
            dbc.Col(dbc.Input(id='edit-bet-amount', placeholder='Bet Amount', type='number'), width=6),
            dbc.Col(dbc.Input(id='edit-odds', placeholder='Odds', type='number'), width=6),
        ], className="g-2"),
        dbc.Col(dbc.Input(id='edit-outcome', placeholder='Outcome (leave blank if unsettled)', type='text'), width=12),
    ]),
    dbc.ModalFooter([
        dbc.Button("Save Changes", id='save-edit-btn', color="primary", className="me-2"),
        dbc.Button("Cancel", id='cancel-edit-btn', color="secondary", className="ms-auto")
    ])
], id="edit-modal", is_open=False)

# Delete Modal
delete_modal = dbc.Modal([
    dbc.ModalHeader(dbc.ModalTitle("Confirm Delete")),
    dbc.ModalBody("Are you sure you want to delete this bet? This action cannot be undone."),
    dbc.ModalFooter([
        dbc.Button("Delete", id='confirm-delete-btn', color="danger", className="me-2"),
        dbc.Button("Cancel", id='cancel-delete-btn', color="secondary", className="ms-auto")
    ])
], id="delete-modal", is_open=False)

# View Selections Modal
view_selections_modal = dbc.Modal([
    dbc.ModalHeader(dbc.ModalTitle("Accumulator Picks")),
    dbc.ModalBody(id='selections-details'),
    dbc.ModalFooter(
        dbc.Button("Close", id='close-view-modal', color="secondary")
    )
], id="view-selections-modal", is_open=False)

# Main layout
app.layout = dbc.Container([
    header_with_currency,
    dbc.Tabs([
        dbc.Tab(label="Bet Management", children=[
            bankroll_settings_card,
            dbc.Tabs([
                dbc.Tab(label="Add Bet", children=[add_bet_card]),
                dbc.Tab(label="Manage Bets", children=[update_outcome_card]),
                dbc.Tab(label="Bet History", children=[bets_table_card])
            ])
        ]),
        dbc.Tab(label="Analytics", children=[
            summary_card,
            charts_row,
            analytics_row,
            time_charts_row,
            calendar_row
        ])
    ], className="mt-4 mb-4"),
    edit_modal,
    delete_modal,
    view_selections_modal,
    dcc.Store(id='data-store', data=df.to_dict('records')),
    dcc.Store(id='settings-store', data=settings),
    dcc.Store(id='currency-store', data='USD')
], fluid=True)

# Callback to update currency store
@app.callback(
    Output('currency-store', 'data'),
    Input('currency-dropdown', 'value')
)
def update_currency(value):
    return value if value else 'USD'

# Callback to update settings
@app.callback(
    [Output('settings-store', 'data'),
     Output('settings-feedback', 'children'),
     Output('settings-feedback', 'color'),
     Output('settings-feedback', 'is_open')],
    Input('update-settings-btn', 'n_clicks'),
    [State('initial-bankroll-input', 'value'),
     State('max-bet-percent-input', 'value'),
     State('settings-store', 'data')]
)
def update_settings(n_clicks, initial_bankroll, max_bet_percent, current_settings):
    if n_clicks > 0:
        if initial_bankroll is not None and max_bet_percent is not None:
            new_settings = current_settings.copy()
            new_settings['initial_bankroll'] = float(initial_bankroll)
            new_settings['max_bet_percent'] = float(max_bet_percent)
            save_settings(new_settings)
            return new_settings, "Settings updated successfully!", "success", True
        else:
            return no_update, "Please enter both values.", "danger", True
    return no_update, "", "info", False

# Callback to toggle add inputs
@app.callback(
    [Output('single-collapse', 'is_open'),
     Output('acc-collapse', 'is_open')],
    Input('bet-type-select', 'value')
)
def toggle_add_inputs(bet_type):
    return bet_type == 'Single', bet_type == 'Accumulator'

# Callback to toggle edit inputs
@app.callback(
    [Output('edit-single-collapse', 'is_open'),
     Output('edit-acc-collapse', 'is_open')],
    Input('edit-wager-type', 'value')
)
def toggle_edit_inputs(bet_type):
    return bet_type == 'Single', bet_type == 'Accumulator'

# Callback to add bet
@app.callback(
    [Output('add-feedback', 'children'),
     Output('add-feedback', 'color'),
     Output('add-feedback', 'is_open'),
     Output('risky-wager-alert', 'children'),
     Output('risky-wager-alert', 'is_open'),
     Output('data-store', 'data'),
     Output('match-input', 'value'),
     Output('prediction-input', 'value'),
     Output('selections-input', 'value'),
     Output('bet-amount-input', 'value'),
     Output('odds-input', 'value')],
    [Input('add-bet-btn', 'n_clicks')],
    [State('bet-type-select', 'value'),
     State('match-input', 'value'),
     State('prediction-input', 'value'),
     State('selections-input', 'value'),
     State('bet-amount-input', 'value'),
     State('odds-input', 'value'),
     State('data-store', 'data'),
     State('settings-store', 'data'),
     State('currency-store', 'data')]
)
def add_bet(n_clicks, wager_type, match, prediction, selections_text, bet_amount, odds_input, data, settings, currency):
    symbols = {'NLE': 'Le', 'USD': '$', 'EUR': '€'}
    symbol = symbols.get(currency, '$')
    if n_clicks > 0 and bet_amount:
        bet_amount = float(bet_amount)
        # Bankroll check
        df_temp = pd.DataFrame(data)
        total_profit = df_temp['profit_loss'].sum() if not df_temp.empty else 0
        current_bankroll = settings['initial_bankroll'] + total_profit
        max_allowed_bet = (settings['max_bet_percent'] / 100) * current_bankroll
        is_risky = bet_amount > max_allowed_bet
        risky_msg = f"Warning: Bet amount ({symbol}{bet_amount:.2f}) exceeds {settings['max_bet_percent']}% of current bankroll ({symbol}{current_bankroll:.2f}). Max allowed: {symbol}{max_allowed_bet:.2f}"

        if wager_type == 'Single':
            if not match or not prediction or not odds_input:
                return "Missing match, prediction, or odds for Single bet.", "danger", True, "", False, no_update, no_update, no_update, no_update, no_update, no_update
            odds = float(odds_input)
            selections = [{'match': match, 'prediction': prediction, 'odds': odds}]
            display_match = match
            display_prediction = prediction
        else:
            if not selections_text:
                return "Missing selections for Accumulator.", "danger", True, "", False, no_update, no_update, no_update, no_update, no_update, no_update
            lines = [line.strip() for line in selections_text.split('\n') if line.strip()]
            selections = []
            for line in lines:
                words = line.split()
                if len(words) < 3:
                    continue
                try:
                    odds = float(words[-1])
                    pred = words[-2]
                    m = ' '.join(words[:-2])
                    selections.append({'match': m, 'prediction': pred, 'odds': odds})
                except ValueError:
                    continue
            if not selections:
                return "Invalid selections format for Accumulator. Each line should be 'Match Prediction Odds'.", "danger", True, "", False, no_update, no_update, no_update, no_update, no_update, no_update
            display_prediction = "Accumulator Win"
            total_odds = reduce(operator.mul, [s['odds'] for s in selections], 1.0)
            display_match = f"Accumulator ({len(selections)} selections)"
            odds = total_odds
        df_new_temp = pd.DataFrame(data)
        max_slip = df_new_temp['slip_no'].max() if not df_new_temp.empty and not df_new_temp['slip_no'].isna().all() else 0
        slip_no = max_slip + 1
        new_bet = {
            'date': datetime.now().isoformat(),
            'match': display_match,
            'prediction': display_prediction,
            'bet_amount': bet_amount,
            'odds': odds,
            'outcome': None,
            'result_amount': 0.0,
            'profit_loss': 0.0,
            'wager_type': wager_type,
            'selections': selections,
            'slip_no': slip_no,
            'status': 'Pending'
        }
        df_new = pd.DataFrame(data)
        df_new = df_new.astype({
            'bet_amount': 'float64',
            'odds': 'float64',
            'result_amount': 'float64',
            'profit_loss': 'float64'
        })
        df_new = pd.concat([df_new, pd.DataFrame([new_bet])], ignore_index=True)
        df_new = renumber_slips(df_new)
        save_data(df_new)
        feedback_msg = f"{wager_type} bet added for {display_match}!"
        feedback_color = "warning" if is_risky else "success"
        return feedback_msg, feedback_color, True, risky_msg if is_risky else "", is_risky, df_new.to_dict('records'), '', '', '', '', ''
    return "", "info", False, "", False, no_update, no_update, no_update, no_update, no_update, no_update

# Callback to open edit modal
@app.callback(
    [Output('edit-modal', 'is_open', allow_duplicate=True),
     Output('edit-wager-type', 'value'),
     Output('edit-match', 'value'),
     Output('edit-prediction', 'value'),
     Output('edit-bet-amount', 'value'),
     Output('edit-odds', 'value'),
     Output('edit-outcome', 'value'),
     Output('edit-selections-input', 'value')],
    Input('edit-bet-btn', 'n_clicks'),
    [State('bets-table-update', 'selected_rows'),
     State('data-store', 'data')],
    prevent_initial_call=True
)
def open_edit_modal(n_clicks, selected_rows, data):
    if n_clicks > 0 and selected_rows and selected_rows[0] is not None:
        idx = selected_rows[0]
        row = pd.DataFrame(data).iloc[idx]
        wager_type = row['wager_type']
        if wager_type == 'Single':
            match_val = row['match']
            pred_val = row['prediction']
            sel_val = ''
        else:
            match_val = ''
            pred_val = ''
            sel_val = '\n'.join(f"{s.get('match', '')} {s.get('prediction', '')} {s.get('odds', 1.0):.2f}" for s in row['selections'])
        outcome_val = row['outcome'] if pd.notna(row['outcome']) else ''
        return True, wager_type, match_val, pred_val, row['bet_amount'], row['odds'], outcome_val, sel_val
    return False, 'Single', '', '', 0, 0, '', ''

# Callback to close edit modal
@app.callback(
    Output('edit-modal', 'is_open', allow_duplicate=True),
    Input('cancel-edit-btn', 'n_clicks'),
    prevent_initial_call=True
)
def close_edit_modal(n_clicks):
    return False

# Callback to save edit
@app.callback(
    [Output('edit-modal', 'is_open', allow_duplicate=True),
     Output('data-store', 'data', allow_duplicate=True),
     Output('bets-table-update', 'data', allow_duplicate=True),
     Output('action-feedback', 'children', allow_duplicate=True),
     Output('action-feedback', 'color', allow_duplicate=True),
     Output('action-feedback', 'is_open', allow_duplicate=True)],
    Input('save-edit-btn', 'n_clicks'),
    [State('edit-wager-type', 'value'),
     State('edit-match', 'value'),
     State('edit-prediction', 'value'),
     State('edit-bet-amount', 'value'),
     State('edit-odds', 'value'),
     State('edit-outcome', 'value'),
     State('edit-selections-input', 'value'),
     State('data-store', 'data'),
     State('bets-table-update', 'selected_rows'),
     State('currency-store', 'data')],
    prevent_initial_call=True
)
def save_edit(n_clicks, wager_type, match, prediction, bet_amount, odds_input, outcome_input, selections_text, data, selected_rows, currency):
    symbols = {'NLE': 'Le', 'USD': '$', 'EUR': '€'}
    symbol = symbols.get(currency, '$')
    if n_clicks > 0 and selected_rows and selected_rows[0] is not None and bet_amount:
        idx = selected_rows[0]
        df_new = pd.DataFrame(data)
        df_new = df_new.astype({
            'bet_amount': 'float64',
            'odds': 'float64',
            'result_amount': 'float64',
            'profit_loss': 'float64'
        })
        bet_amount = float(bet_amount)
        row = df_new.iloc[idx]
        prev_wager_type = df_new.at[idx, 'wager_type']
        if wager_type == 'Single':
            if not match or not prediction or not odds_input:
                return no_update, no_update, no_update, "Missing match, prediction, or odds for Single bet.", "danger", True
            odds = float(odds_input)
            selections = [{'match': match, 'prediction': prediction, 'odds': odds}]
            display_match = match
            display_prediction = prediction
        else:
            if not selections_text:
                return no_update, no_update, no_update, "Missing selections for Accumulator.", "danger", True
            lines = [line.strip() for line in selections_text.split('\n') if line.strip()]
            selections = []
            for line in lines:
                words = line.split()
                if len(words) < 3:
                    continue
                try:
                    odds = float(words[-1])
                    pred = words[-2]
                    m = ' '.join(words[:-2])
                    selections.append({'match': m, 'prediction': pred, 'odds': odds})
                except ValueError:
                    continue
            if not selections:
                return no_update, no_update, no_update, "Invalid selections format for Accumulator. Each line should be 'Match Prediction Odds'.", "danger", True
            display_prediction = "Accumulator Win"
            total_odds = reduce(operator.mul, [s['odds'] for s in selections], 1.0)
            odds = total_odds
            display_match = f"Accumulator ({len(selections)} selections)"
        # Update fields
        df_new.at[idx, 'wager_type'] = wager_type
        df_new.at[idx, 'match'] = display_match
        df_new.at[idx, 'prediction'] = display_prediction
        df_new.at[idx, 'bet_amount'] = bet_amount
        df_new.at[idx, 'odds'] = odds
        df_new.at[idx, 'selections'] = selections
        # Handle outcome and status
        if outcome_input == 'Pending' or not outcome_input:
            new_outcome = None
            result_amount = 0.0
            profit_loss = 0.0
            status = 'Pending'
        elif wager_type == 'Accumulator':
            if outcome_input in ['Win', 'Loss']:
                new_outcome = outcome_input
                if outcome_input == 'Win':
                    result_amount = round(bet_amount * odds, 2)
                    profit_loss = round(result_amount - bet_amount, 2)
                    status = 'Win'
                else:
                    result_amount = 0.0
                    profit_loss = round(-bet_amount, 2)
                    status = 'Loss'
            else:
                return no_update, no_update, no_update, "Invalid outcome for Accumulator. Use 'Win', 'Loss', or 'Pending'.", "danger", True
        else:  # Single
            prediction = display_prediction
            if outcome_input in ['Win', 'Loss']:
                if outcome_input == 'Win':
                    new_outcome = prediction
                    result_amount = round(bet_amount * odds, 2)
                    profit_loss = round(result_amount - bet_amount, 2)
                    status = 'Win'
                else:
                    new_outcome = 'Loss'  # dummy
                    result_amount = 0.0
                    profit_loss = round(-bet_amount, 2)
                    status = 'Loss'
            elif outcome_input == prediction:
                new_outcome = outcome_input
                result_amount = round(bet_amount * odds, 2)
                profit_loss = round(result_amount - bet_amount, 2)
                status = 'Win'
            else:
                new_outcome = outcome_input
                result_amount = 0.0
                profit_loss = round(-bet_amount, 2)
                status = 'Loss'
        df_new.at[idx, 'outcome'] = new_outcome
        df_new.at[idx, 'result_amount'] = result_amount
        df_new.at[idx, 'profit_loss'] = profit_loss
        df_new.at[idx, 'status'] = status
        df_new = renumber_slips(df_new)
        save_data(df_new)
        display_data = get_display_data(df_new, currency)
        feedback_msg = f"Bet updated to {status}!" if status != 'Pending' else "Bet set to Pending!"
        return False, df_new.to_dict('records'), display_data, feedback_msg, "success" if status == 'Win' else "danger" if status == 'Loss' else "info", True
    return no_update, no_update, no_update, "Update failed - incomplete data.", "danger", True

# Callback to open delete modal
@app.callback(
    Output('delete-modal', 'is_open', allow_duplicate=True),
    Input('delete-bet-btn', 'n_clicks'),
    [State('bets-table-update', 'selected_rows')],
    prevent_initial_call=True
)
def open_delete_modal(n_clicks, selected_rows):
    if n_clicks > 0 and selected_rows and selected_rows[0] is not None:
        return True
    return False

# Callback to close delete modal
@app.callback(
    Output('delete-modal', 'is_open', allow_duplicate=True),
    Input('cancel-delete-btn', 'n_clicks'),
    prevent_initial_call=True
)
def close_delete_modal(n_clicks):
    return False

# Callback to confirm delete
@app.callback(
    [Output('delete-modal', 'is_open', allow_duplicate=True),
     Output('data-store', 'data', allow_duplicate=True),
     Output('bets-table-update', 'data', allow_duplicate=True),
     Output('action-feedback', 'children', allow_duplicate=True),
     Output('action-feedback', 'color', allow_duplicate=True),
     Output('action-feedback', 'is_open', allow_duplicate=True)],
    Input('confirm-delete-btn', 'n_clicks'),
    [State('data-store', 'data'),
     State('bets-table-update', 'selected_rows'),
     State('currency-store', 'data')],
    prevent_initial_call=True
)
def confirm_delete(n_clicks, data, selected_rows, currency):
    if n_clicks > 0 and selected_rows and selected_rows[0] is not None:
        idx = selected_rows[0]
        df_new = pd.DataFrame(data)
        df_new = df_new.astype({
            'bet_amount': 'float64',
            'odds': 'float64',
            'result_amount': 'float64',
            'profit_loss': 'float64'
        })
        df_new = df_new.drop(idx).reset_index(drop=True)
        df_new = renumber_slips(df_new)
        save_data(df_new)
        display_data = get_display_data(df_new, currency)
        return False, df_new.to_dict('records'), display_data, "Bet deleted successfully!", "warning", True
    return no_update, no_update, no_update, "Delete failed.", "danger", True

# Callback to update outcome
@app.callback(
    [Output('action-feedback', 'children'),
     Output('action-feedback', 'color'),
     Output('action-feedback', 'is_open'),
     Output('data-store', 'data', allow_duplicate=True),
     Output('bets-table-update', 'data', allow_duplicate=True)],
    [Input('update-outcome-btn', 'n_clicks')],
    [State('bets-table-update', 'selected_rows'),
     State('outcome-input', 'value'),
     State('data-store', 'data'),
     State('currency-store', 'data')],
    prevent_initial_call=True
)
def update_outcome(n_clicks, selected_rows, outcome_input, data, currency):
    symbols = {'NLE': 'Le', 'USD': '$', 'EUR': '€'}
    symbol = symbols.get(currency, '$')
    if n_clicks > 0 and selected_rows and selected_rows[0] is not None and outcome_input:
        idx = selected_rows[0]
        df_new = pd.DataFrame(data)
        df_new = df_new.astype({
            'bet_amount': 'float64',
            'odds': 'float64',
            'result_amount': 'float64',
            'profit_loss': 'float64'
        })
        row = df_new.iloc[idx]
        wager_type = row['wager_type']
        bet_amount = row['bet_amount']
        odds = row['odds']
        prediction = row['prediction']
        if outcome_input == 'Pending':
            new_outcome = None
            result_amount = 0.0
            profit_loss = 0.0
            status = 'Pending'
            feedback_color = 'info'
        elif wager_type == 'Accumulator':
            if outcome_input in ['Win', 'Loss']:
                new_outcome = outcome_input
                if outcome_input == 'Win':
                    result_amount = round(bet_amount * odds, 2)
                    profit_loss = round(result_amount - bet_amount, 2)
                    status = 'Win'
                    feedback_color = 'success'
                else:
                    result_amount = 0.0
                    profit_loss = round(-bet_amount, 2)
                    status = 'Loss'
                    feedback_color = 'danger'
            else:
                return "Invalid outcome for Accumulator. Use 'Win', 'Loss', or 'Pending'.", "danger", True, no_update, no_update
        else:  # Single
            if outcome_input in ['Win', 'Loss']:
                if outcome_input == 'Win':
                    new_outcome = prediction
                    result_amount = round(bet_amount * odds, 2)
                    profit_loss = round(result_amount - bet_amount, 2)
                    status = 'Win'
                    feedback_color = 'success'
                else:
                    new_outcome = 'Loss'  # dummy != prediction
                    result_amount = 0.0
                    profit_loss = round(-bet_amount, 2)
                    status = 'Loss'
                    feedback_color = 'danger'
            elif outcome_input == prediction:
                new_outcome = outcome_input
                result_amount = round(bet_amount * odds, 2)
                profit_loss = round(result_amount - bet_amount, 2)
                status = 'Win'
                feedback_color = 'success'
            else:
                new_outcome = outcome_input
                result_amount = 0.0
                profit_loss = round(-bet_amount, 2)
                status = 'Loss'
                feedback_color = 'danger'
        df_new.at[idx, 'outcome'] = new_outcome
        df_new.at[idx, 'result_amount'] = result_amount
        df_new.at[idx, 'profit_loss'] = profit_loss
        df_new.at[idx, 'status'] = status
        df_new = renumber_slips(df_new)
        save_data(df_new)
        display_data = get_display_data(df_new, currency)
        if status == 'Pending':
            feedback_msg = f"Set bet {idx} to Pending"
        else:
            feedback_msg = f"Updated bet {idx} to {status}, Profit/Loss: {symbol}{profit_loss:+.2f}"
        return feedback_msg, feedback_color, True, df_new.to_dict('records'), display_data
    return "", "info", False, no_update, no_update

# Callback to view selections
@app.callback(
    [Output('view-selections-modal', 'is_open', allow_duplicate=True),
     Output('selections-details', 'children', allow_duplicate=True)],
    [Input('bets-table', 'active_cell'),
     Input('bets-table-update', 'active_cell'),
     Input('close-view-modal', 'n_clicks')],
    [State('data-store', 'data')],
    prevent_initial_call=True
)
def view_selections(active1, active2, close_clicks, data):
    ctx = dash.callback_context
    if not ctx.triggered:
        return no_update, no_update
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
    if trigger_id == 'close-view-modal':
        return False, no_update
    if trigger_id == 'bets-table':
        active_cell = active1
    elif trigger_id == 'bets-table-update':
        active_cell = active2
    else:
        return no_update, no_update
    if active_cell and active_cell.get('column_id') == 'selections':
        idx = active_cell['row']
        row = pd.DataFrame(data).iloc[idx]
        if row['wager_type'] == 'Accumulator' and isinstance(row['selections'], list) and len(row['selections']) > 0:
            picks_list = [
                dbc.ListGroupItem(f"Match: {s.get('match', 'N/A')} | Prediction: {s.get('prediction', 'N/A')} | Odds: {round(s.get('odds', 1.0), 2):.2f}") 
                for s in row['selections']
            ]
            picks = dbc.ListGroup(picks_list, flush=True)
            return True, picks
    return False, no_update

# Callback to update tables and summary
@app.callback(
    [Output('bets-table', 'data'),
     Output('bets-table-update', 'data'),
     Output('summary-stats', 'children'),
     Output('profit-bar-chart', 'figure'),
     Output('cumulative-line-chart', 'figure'),
     Output('bet-category-bar-chart', 'figure'),
     Output('yearly-chart', 'figure'),
     Output('monthly-chart', 'figure'),
     Output('weekly-chart', 'figure'),
     Output('dow-chart', 'figure'),
     Output('calendar-heatmap', 'figure')],
    [Input('data-store', 'data'), Input('currency-store', 'data'), Input('settings-store', 'data')]
)
def update_display(data, currency, settings):
    df_new = pd.DataFrame(data)
    df_new = df_new.astype({
        'bet_amount': 'float64',
        'odds': 'float64',
        'result_amount': 'float64',
        'profit_loss': 'float64'
    })
    if not df_new.empty:
        df_new['date'] = pd.to_datetime(df_new['date'])
        df_new['status'] = np.where(df_new['outcome'].isna(), 'Pending', np.where(df_new['profit_loss'] > 0, 'Win', 'Loss'))
    table_data = get_display_data(df_new, currency)
    
    symbols = {'NLE': 'Le', 'USD': '$', 'EUR': '€'}
    symbol = symbols.get(currency, '$')
    
    # Compute bet_category first
    if not df_new.empty:
        df_new['bet_category'] = df_new.apply(categorize_bet_type, axis=1)
    
    # Compute is_win for settled bets
    settled = pd.DataFrame()
    unsettled_bets = 0
    if not df_new.empty:
        settled = df_new[df_new['outcome'].notna()].copy()
        unsettled_bets = len(df_new) - len(settled)
        if not settled.empty:
            mask_single_win = (settled['wager_type'] == 'Single') & (settled['outcome'] == settled['prediction'])
            mask_acc_win = (settled['wager_type'] == 'Accumulator') & (settled['outcome'] == 'Win')
            settled['is_win'] = (mask_single_win | mask_acc_win).astype(int)
    
    # Advanced Summary
    if not df_new.empty:
        total_bets = len(df_new)
        settled_bets = len(settled)
        wins = settled['is_win'].sum() if not settled.empty else 0
        losses = settled_bets - wins if settled_bets > 0 else 0
        win_rate = (wins / settled_bets * 100) if settled_bets > 0 else 0
        total_profit = round(df_new['profit_loss'].sum(), 2)
        total_staked = round(df_new['bet_amount'].sum(), 2)
        roi = round((total_profit / total_staked * 100), 1) if total_staked > 0 else 0
        avg_odds = round(df_new['odds'].mean(), 2)
        avg_bet = round(df_new['bet_amount'].mean(), 2)
        max_win_streak, max_loss_streak = get_streaks(df_new)
        
        # Bankroll metrics
        current_bankroll = round(settings['initial_bankroll'] + total_profit, 2)
        bankroll_health = round((current_bankroll / settings['initial_bankroll']) * 100, 1) if settings['initial_bankroll'] > 0 else 0
        
        # Bet categories
        type_profit = settled.groupby('bet_category')['profit_loss'].sum().round(2).reset_index()
        
        summary = [
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Total Bets"), html.P(total_bets)], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Settled Bets"), html.P(settled_bets)], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Unsettled Bets"), html.P(unsettled_bets)], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Wins"), html.P(wins)], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Losses"), html.P(losses)], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Win Rate"), html.P(f"{win_rate:.1f}%")], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("ROI"), html.P(f"{roi:.1f}%")], className="text-center", style={'color': 'green' if roi >= 0 else 'red'})), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Total P/L"), html.P(f"{symbol}{total_profit:+.2f}")], className="text-center", style={'color': 'green' if total_profit >= 0 else 'red'})), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Total Staked"), html.P(f"{symbol}{total_staked:.2f}")], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Initial Bankroll"), html.P(f"{symbol}{settings['initial_bankroll']:.2f}")], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Current Bankroll"), html.P(f"{symbol}{current_bankroll:.2f}")], className="text-center", style={'color': 'green' if current_bankroll >= settings['initial_bankroll'] else 'red'})), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Bankroll Health"), html.P(f"{bankroll_health:.1f}%")], className="text-center", style={'color': 'green' if bankroll_health >= 100 else 'red'})), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Avg Odds"), html.P(f"{avg_odds:.2f}")], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Avg Bet"), html.P(f"{symbol}{avg_bet:.2f}")], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Max Win Streak"), html.P(max_win_streak)], className="text-center")), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Max Loss Streak"), html.P(max_loss_streak)], className="text-center")), width=3),
        ]
    else:
        summary = [dbc.Col(html.P("No bets yet.", className="text-center"), width=12)]
    
    # Profit/Loss Bar Chart
    if not df_new.empty:
        profit_fig = px.bar(df_new.sort_values('date'), x='date', y='profit_loss', 
                            title="Profit/Loss per Bet", color='profit_loss', 
                            color_continuous_scale=['red', 'green'],
                            labels={'profit_loss': f'Profit/Loss ({symbol})', 'date': 'Date'})
        profit_fig.update_layout(showlegend=False, xaxis_title="Date", yaxis_title=f'Profit/Loss ({symbol})')
    else:
        profit_fig = px.bar(title="No data for chart")
    
    # Cumulative P/L Line Chart
    if not df_new.empty:
        df_sorted = df_new.sort_values('date').copy()
        df_sorted['cumulative_pl'] = df_sorted['profit_loss'].cumsum().round(2)
        cum_fig = px.line(df_sorted, x='date', y='cumulative_pl', 
                          title="Cumulative Profit/Loss Over Time",
                          labels={'cumulative_pl': f'Cumulative P/L ({symbol})', 'date': 'Date'})
        cum_fig.update_layout(showlegend=False, xaxis_title="Date", yaxis_title=f'Cumulative P/L ({symbol})')
    else:
        cum_fig = px.line(title="No data for chart")
    
    # Profit by Bet Category Bar Chart
    type_fig = px.bar(title="No data for chart")
    if not df_new.empty and 'type_profit' in locals() and not type_profit.empty:
        type_fig = px.bar(type_profit, x='bet_category', y='profit_loss',
                          title="Total Profit by Bet Type",
                          color='profit_loss',
                          color_continuous_scale=['red', 'green'],
                          labels={'profit_loss': f'Total Profit/Loss ({symbol})', 'bet_category': 'Bet Type'})
        type_fig.update_layout(xaxis_title="Bet Type", yaxis_title=f'Total Profit/Loss ({symbol})')
    
    # Time-based charts
    yearly_fig = px.bar(title="No data for chart")
    monthly_fig = px.bar(title="No data for chart")
    weekly_fig = px.bar(title="No data for chart")
    dow_fig = px.bar(title="No data for chart")
    
    if not settled.empty:
        settled['year'] = settled['date'].dt.year
        settled['year_month'] = settled['date'].dt.to_period('M').astype(str)
        settled['year_week'] = settled['date'].dt.strftime('%Y-W%W')
        settled['day_of_week'] = settled['date'].dt.day_name()
        
        # Yearly P/L and Wins/Losses
        yearly_stats = settled.groupby('year').agg({
            'profit_loss': 'sum',
            'is_win': ['sum', 'count']
        }).round(2)
        yearly_stats.columns = ['profit_loss', 'wins', 'total_bets']
        yearly_stats['losses'] = yearly_stats['total_bets'] - yearly_stats['wins']
        yearly_stats = yearly_stats.reset_index()
        if not yearly_stats.empty:
            yearly_fig = px.bar(yearly_stats, x='year', y='profit_loss',
                                title=f"Yearly P/L ({symbol})",
                                color='profit_loss', color_continuous_scale=['red', 'green'])
            yearly_fig.update_layout(xaxis_title="Year", yaxis_title=f'P/L ({symbol})')
        
        # Monthly P/L
        monthly_stats = settled.groupby('year_month')['profit_loss'].sum().round(2).reset_index()
        if not monthly_stats.empty:
            monthly_fig = px.bar(monthly_stats, x='year_month', y='profit_loss',
                                 title=f"Monthly P/L ({symbol})",
                                 color='profit_loss', color_continuous_scale=['red', 'green'])
            monthly_fig.update_layout(xaxis_title="Year-Month", yaxis_title=f'P/L ({symbol})', xaxis_tickangle=45)
        
        # Weekly P/L
        weekly_stats = settled.groupby('year_week')['profit_loss'].sum().round(2).reset_index()
        if not weekly_stats.empty:
            weekly_fig = px.bar(weekly_stats, x='year_week', y='profit_loss',
                                title=f"Weekly P/L ({symbol})",
                                color='profit_loss', color_continuous_scale=['red', 'green'])
            weekly_fig.update_layout(xaxis_title="Year-Week", yaxis_title=f'P/L ({symbol})', xaxis_tickangle=45)
        
        # Day of Week P/L
        dow_stats = settled.groupby('day_of_week')['profit_loss'].sum().round(2).reset_index()
        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        dow_stats['day_of_week'] = pd.Categorical(dow_stats['day_of_week'], categories=day_order, ordered=True)
        dow_stats = dow_stats.sort_values('day_of_week')
        if not dow_stats.empty:
            dow_fig = px.bar(dow_stats, x='day_of_week', y='profit_loss',
                             title=f"P/L by Day of Week ({symbol})",
                             color='profit_loss', color_continuous_scale=['red', 'green'])
            dow_fig.update_layout(xaxis_title="Day of Week", yaxis_title=f'P/L ({symbol})')
    
    # Calendar Heatmap
    calendar_fig = go.Figure()
    if not settled.empty:
        settled['day'] = settled['date'].dt.day
        settled['month_year'] = settled['date'].dt.to_period('M').astype(str)
        unique_months = sorted(settled['month_year'].unique())
        max_day = 31
        matrix = []
        month_labels = []
        for month in unique_months:
            month_data = settled[settled['month_year'] == month].groupby('day')['profit_loss'].sum().reindex(range(1, max_day+1), fill_value=0).round(2)
            matrix.append(month_data.values)
            month_labels.append(month)
        if matrix:
            calendar_fig = go.Figure(data=go.Heatmap(
                z=matrix,
                x=list(range(1, max_day+1)),
                y=month_labels,
                colorscale='RdYlGn',
                zmid=0,
                colorbar=dict(title=f"Daily P/L ({symbol})")
            ))
            calendar_fig.update_layout(
                title="Monthly Calendar Heatmap: Daily Profit/Loss",
                xaxis_title="Day of Month",
                yaxis_title="Month-Year",
                yaxis=dict(autorange="reversed"),
                height=300
            )
    
    return table_data, table_data, summary, profit_fig, cum_fig, type_fig, yearly_fig, monthly_fig, weekly_fig, dow_fig, calendar_fig

# For production deployment
server = app.server

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)
