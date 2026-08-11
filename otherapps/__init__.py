"""
otherapps – independent sub-applications mounted under /otherapps
Each sub-app is a self-contained Flask Blueprint with its own SQLite DB.
"""

def register_blueprints(app):
    from .docsformatter import docsformatter_bp
    from .meetingsummarizer import meetingsummarizer_bp
    from .onlineidval import onlineidval_bp
    from .csvany import csvany_bp
    from .actibook import actibook_bp

    app.register_blueprint(docsformatter_bp,    url_prefix='/otherapps/docsformatter')
    app.register_blueprint(meetingsummarizer_bp, url_prefix='/otherapps/meetingsummarizer')
    app.register_blueprint(onlineidval_bp,       url_prefix='/otherapps/onlineidval')
    app.register_blueprint(csvany_bp,            url_prefix='/otherapps/csvany')
    app.register_blueprint(actibook_bp,          url_prefix='/otherapps/actibook')
