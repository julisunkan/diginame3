# Overview

This is a Flask-based blog CMS (Content Management System) that provides a simple blogging platform with public viewing capabilities and admin management features. The application allows visitors to browse blog posts on the homepage and view individual posts in detail, while administrators can log in to create, edit, and delete posts through a dedicated admin dashboard.

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Web Framework
- **Flask**: Lightweight Python web framework chosen for rapid development and simplicity
- **Jinja2 Templates**: Server-side rendering with template inheritance using a base template system
- **Session-based Authentication**: Simple session management for admin authentication without complex user roles

## Database Layer
- **SQLAlchemy ORM**: Object-relational mapping for database interactions with Flask-SQLAlchemy extension
- **SQLite**: File-based database stored in `instance/blog.db` for development simplicity
- **Single Entity Model**: Post model with basic fields (id, title, content, created_at)

## Frontend Architecture
- **Server-Side Rendering**: Traditional MVC pattern with Flask rendering HTML templates
- **Bootstrap 5**: CSS framework for responsive design and UI components
- **Animate.css**: CSS animation library for enhanced user experience
- **Custom CSS**: Gradient backgrounds and glass-morphism effects for modern styling

## Authentication System
- **Simple Admin Authentication**: Single hardcoded admin user credentials
- **Session Management**: Flask sessions with 24-hour expiration
- **Decorator-based Protection**: Custom `login_required` decorator for securing admin routes

## Application Structure
- **Route Separation**: Clear separation between public routes (blog viewing) and admin routes (content management)
- **Template Hierarchy**: Base template with child templates for different pages
- **Static Assets**: CSS files served from static directory
- **Instance Directory**: Automatic creation for database file storage

# External Dependencies

## Python Packages
- **Flask 3.1.2**: Core web framework
- **Flask-SQLAlchemy 3.1.1**: Database ORM integration

## Frontend Libraries (CDN)
- **Bootstrap 5.1.3**: CSS framework and JavaScript components
- **Animate.css 4.1.1**: CSS animation library

## Environment Variables
- **SESSION_SECRET**: Optional environment variable for Flask session security (defaults to development key)

## Database
- **SQLite**: Self-contained database file, no external database server required