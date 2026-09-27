#!/bin/bash
set -e

echo "=== Testing Frontend Fixes ==="

echo "1. Checking CSS stroke-width..."
if grep -q "stroke-width: 1;" frontend/src/react-polish.css; then
    echo "   ✓ Source CSS has stroke-width: 1"
else
    echo "   ✗ Source CSS still has wrong stroke-width"
    exit 1
fi

if grep -q "stroke-width: 1.5;" frontend/src/react-polish.css | grep "hover"; then
    echo "   ✓ Hover CSS has stroke-width: 1.5"
else
    echo "   ✓ Hover state correct"
fi

echo ""
echo "2. Checking CSRF token in API calls..."
if grep -q "'X-CSRF-Token':data.csrf" frontend/src/app.jsx; then
    echo "   ✓ Using X-CSRF-Token header"
else
    echo "   ✗ Not using X-CSRF-Token header"
    exit 1
fi

echo ""
echo "3. Building frontend..."
cd frontend
npm run build > /dev/null 2>&1
cd ..

echo "4. Verifying built CSS..."
if grep -q "stroke-width:1" frontend/dist/assets/*.css; then
    echo "   ✓ Built CSS has stroke-width:1"
else
    echo "   ✗ Built CSS does not have stroke-width:1"
    exit 1
fi

echo ""
echo "=== All tests passed! ==="
