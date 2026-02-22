const express = require('express');
const puppeteer = require('puppeteer');

const app = express();
const port = 3001;

app.use(express.json());

app.post('/screenshot', async (req, res) => {
    const { url } = req.body;

    if (!url) {
        return res.status(400).json({ error: 'URL is required' });
    }

    let browser = null;
    try {
        console.log(`Taking screenshot of: ${url}`);
        browser = await puppeteer.launch({
            headless: "new",
            args: ['--no-sandbox', '--disable-setuid-sandbox']
        });
        const page = await browser.newPage();

        // Set a reasonable viewport
        await page.setViewport({ width: 1280, height: 720 });

        // Navigate to the URL
        await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });

        // Take a full page screenshot
        const screenshot = await page.screenshot({ encoding: 'base64', fullPage: true });

        console.log(`Screenshot taken successfully.`);
        res.json({ screenshot: `data:image/png;base64,${screenshot}` });

    } catch (error) {
        console.error('Error taking screenshot:', error);
        res.status(500).json({ error: 'Failed to take screenshot', details: error.message });
    } finally {
        if (browser) {
            await browser.close();
        }
    }
});

app.listen(port, () => {
    console.log(`Screenshot service running at http://localhost:${port}`);
});
