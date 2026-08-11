# PythonAnywhere Deployment Instructions

This guide will help you deploy the Flask Blog CMS to PythonAnywhere.

## Prerequisites

- PythonAnywhere account (free or paid)
- Git repository with your Flask application code

## Step-by-Step Deployment

### 1. Upload Your Code

**Option A: Using Git (Recommended)**
```bash
cd ~
git clone https://github.com/yourusername/your-blog-repo.git blogcms
cd blogcms
```

**Option B: Using Files Tab**
- Upload all project files to `/home/yourusername/blogcms/`

### 2. Create Virtual Environment

Open a Bash console and run:
```bash
mkvirtualenv --python=/usr/bin/python3.11 blogcms-env
workon blogcms-env
cd ~/blogcms
pip install -r requirements.txt
```

### 3. Configure Web App

1. Go to the **Web** tab in your PythonAnywhere dashboard
2. Click **"Add a new web app"**
3. Choose **"Manual configuration"**
4. Select **Python 3.11** (or your preferred version)

### 4. Update WSGI File

1. In the **Web** tab, click on the WSGI configuration file link
2. Replace the contents with the provided `wsgi.py` file:

```python
#!/usr/bin/python3

import sys
import os

# Add your project directory to the sys.path
# Replace 'yourusername' with your actual PythonAnywhere username
project_home = '/home/yourusername/blogcms'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Set environment variables for production
os.environ.setdefault('FLASK_ENV', 'production')

# Import flask app instance but rename it to 'application' for WSGI
from app import app as application

if __name__ == "__main__":
    application.run()
```

**Important**: Replace `yourusername` with your actual PythonAnywhere username.

### 5. Configure Virtual Environment

In the **Web** tab:
- Find the **"Virtualenv"** section
- Enter: `/home/yourusername/.virtualenvs/blogcms-env`

### 6. Set Environment Variables

In the **Web** tab, find the **"Environment variables"** section and add:

| Variable | Value | Notes |
|----------|-------|-------|
| `FLASK_ENV` | `production` | Required for production config |
| `SESSION_SECRET` | `your-secure-random-string` | Generate a secure random string |
| `ADMIN_USERNAME` | `your-admin-username` | Your desired admin username |
| `ADMIN_PASSWORD_HASH` | `your-password-hash` | Generated using the hash script (see below) |

**Important Security Steps:**

1. **Generate a secure session secret:**
```python
import secrets
print(secrets.token_hex(32))
```

2. **Generate a secure admin password hash:**
```bash
workon blogcms-env
cd ~/blogcms
python generate_admin_hash.py
```

Follow the prompts to create a secure password hash. Copy the generated hash and use it as the `ADMIN_PASSWORD_HASH` environment variable.

**⚠️ Security Note:** Never use `ADMIN_PASSWORD` in production. Always use `ADMIN_PASSWORD_HASH` for secure password storage.

### 7. Configure Static Files (Optional)

If you want to serve static files through PythonAnywhere's static file server:

In the **Web** tab, **"Static files"** section:
- URL: `/static/`
- Directory: `/home/yourusername/blogcms/static/`

### 8. Initialize Database

In a Bash console:
```bash
workon blogcms-env
cd ~/blogcms
python -c "from app import app; from app import db; app.app_context().push(); db.create_all()"
```

### 9. Reload Web App

In the **Web** tab, click the **"Reload"** button.

## Post-Deployment Checklist

1. **Test the application**: Visit your PythonAnywhere domain
2. **Test admin login**: Go to `/admin/login` and log in with your credentials
3. **Create a test post**: Verify content creation works
4. **Test export/import**: Verify backup functionality works
5. **Check error logs**: Look at error logs if anything doesn't work

## Troubleshooting

### Common Issues

**"Error running WSGI application"**
- Check the error log: `/var/log/yourusername.pythonanywhere.com.error.log`
- Verify all file paths in `wsgi.py` are correct
- Ensure all dependencies are installed in the virtual environment

**Database Issues**
- Make sure the `instance` directory exists and is writable
- Check database file permissions
- Verify database initialization ran successfully

**Static Files Not Loading**
- Verify static file configuration in Web tab
- Check file permissions in static directory
- Ensure CSS/JS files are in the correct location

**Import Errors**
- Verify all packages are installed: `pip list`
- Check Python version compatibility
- Ensure all custom modules are in the correct path

### Viewing Logs

**Error Log:**
```bash
tail -f /var/log/yourusername.pythonanywhere.com.error.log
```

**Access Log:**
```bash
tail -f /var/log/yourusername.pythonanywhere.com.access.log
```

## Security Recommendations

1. **Change default credentials**: Never use default admin credentials in production
2. **Use strong passwords**: Generate secure passwords for admin access
3. **Keep dependencies updated**: Regularly update your `requirements.txt`
4. **Monitor logs**: Regularly check error logs for security issues
5. **Backup regularly**: Use the export feature to backup your content

## Updating Your Application

When you make changes to your code:

1. Update your code (via Git or file upload)
2. Install any new dependencies: `pip install -r requirements.txt`
3. Reload the web app in the Web tab
4. Check error logs for any issues

## Support

- PythonAnywhere Help: https://help.pythonanywhere.com/
- PythonAnywhere Forums: https://www.pythonanywhere.com/forums/
- Flask Documentation: https://flask.palletsprojects.com/