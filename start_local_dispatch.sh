#!/bin/bash
# JOAO Local Dispatch — Quick Start/Stop/Status
# Usage: ./start_local_dispatch.sh [start|stop|status|restart|tunnel-url]

ACTION=${1:-status}

case $ACTION in
    start)
        systemctl --user start joao-dispatch.service
        systemctl --user start joao-tunnel.service
        sleep 3
        echo "Services started. Getting tunnel URL..."
        journalctl --user -u joao-tunnel.service --no-pager -n 20 | grep -oP 'https://[a-z-]+\.trycloudflare\.com'
        ;;
    stop)
        systemctl --user stop joao-tunnel.service
        systemctl --user stop joao-dispatch.service
        echo "Services stopped."
        ;;
    restart)
        systemctl --user restart joao-dispatch.service
        systemctl --user restart joao-tunnel.service
        sleep 3
        echo "Services restarted. New tunnel URL:"
        journalctl --user -u joao-tunnel.service --no-pager -n 20 | grep -oP 'https://[a-z-]+\.trycloudflare\.com'
        ;;
    status)
        echo "=== JOAO Dispatch ==="
        systemctl --user status joao-dispatch.service --no-pager | head -5
        echo ""
        echo "=== JOAO Tunnel ==="
        systemctl --user status joao-tunnel.service --no-pager | head -5
        echo ""
        echo "=== Tunnel URL ==="
        journalctl --user -u joao-tunnel.service --no-pager -n 50 | grep -oP 'https://[a-z-]+\.trycloudflare\.com' | tail -1
        echo ""
        echo "=== Local Health ==="
        curl -s http://localhost:7777/health 2>/dev/null || echo "Not reachable"
        ;;
    tunnel-url)
        journalctl --user -u joao-tunnel.service --no-pager -n 50 | grep -oP 'https://[a-z-]+\.trycloudflare\.com' | tail -1
        ;;
    *)
        echo "Usage: $0 [start|stop|status|restart|tunnel-url]"
        ;;
esac
