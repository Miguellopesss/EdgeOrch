EdgeOrch desktop build

Files:
- EdgeOrch.exe: launch the desktop app
- .env: runtime configuration loaded at startup (if present)
- .env.example: template for a new environment file

Identity:
- CLIENT_ORIGIN=AUTO and CLIENT_AE_NAME=AUTO derive a unique client identity from each user's PC name
- this lets the same build work for different colleagues without rebuilding per person

To rebuild with another environment:
1. Edit the .env file you want to package
2. Run:
   powershell -ExecutionPolicy Bypass -File .\build_edgeorch.ps1 -IncludeEnv

The desktop app always prefers a local .env next to EdgeOrch.exe.
