import os
import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration - Load from environment variables for security
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY")
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# Validate that required environment variables are set
if not TELEGRAM_BOT_TOKEN or not COINGECKO_API_KEY:
    raise ValueError("Missing required environment variables: TELEGRAM_BOT_TOKEN and COINGECKO_API_KEY")

# Store your chat ID (will be set when you start the bot)
USER_CHAT_ID = None

# Cache to avoid sending duplicate projects
sent_projects = set()


class ProjectScanner:
    """Scans for new DeFi and NFT projects"""
    
    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "accept": "application/json",
            "x-cg-demo-api-key": api_key
        }
    
    async def get_new_coins(self):
        """Fetch recently added coins from CoinGecko"""
        url = f"{COINGECKO_BASE_URL}/coins/list?include_platform=true"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        coins = await response.json()
                        # Get the newest coins (last 100 added)
                        return coins[-100:] if len(coins) > 100 else coins
                    else:
                        logger.error(f"CoinGecko API error: {response.status}")
                        return []
        except Exception as e:
            logger.error(f"Error fetching coins: {e}")
            return []
    
    async def get_coin_details(self, coin_id):
        """Get detailed information about a specific coin"""
        url = f"{COINGECKO_BASE_URL}/coins/{coin_id}"
        params = {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "true",
            "developer_data": "false"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.warning(f"Failed to get details for {coin_id}: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching coin details for {coin_id}: {e}")
            return None
    
    async def scan_for_projects(self):
        """Scan for new projects matching criteria"""
        matching_projects = []
        
        # Get list of new coins
        coins = await self.get_new_coins()
        logger.info(f"Scanning {len(coins)} coins...")
        
        # Check each coin (limiting to avoid rate limits)
        for coin in coins[:20]:  # Check 20 newest coins per scan
            coin_id = coin.get('id')
            
            # Skip if already sent
            if coin_id in sent_projects:
                continue
            
            # Get detailed info
            details = await self.get_coin_details(coin_id)
            if not details:
                continue
            
            # Extract social metrics
            community_data = details.get('community_data', {})
            links = details.get('links', {})
            
            twitter_followers = community_data.get('twitter_followers', 0) or 0
            telegram_users = community_data.get('telegram_channel_user_count', 0) or 0
            
            # Check if matches criteria
            if twitter_followers < 200 and telegram_users < 50:
                project_info = {
                    'id': coin_id,
                    'name': details.get('name', 'Unknown'),
                    'symbol': details.get('symbol', '').upper(),
                    'description': details.get('description', {}).get('en', 'No description available')[:500],
                    'twitter_followers': twitter_followers,
                    'telegram_users': telegram_users,
                    'homepage': links.get('homepage', [''])[0] if links.get('homepage') else '',
                    'twitter': links.get('twitter_screen_name', ''),
                    'telegram': links.get('telegram_channel_identifier', ''),
                    'discord': links.get('chat_url', [''])[0] if 'discord' in str(links.get('chat_url', '')).lower() else '',
                    'categories': details.get('categories', []),
                    'contract_address': details.get('contract_address', ''),
                    'market_cap': details.get('market_data', {}).get('market_cap', {}).get('usd', 0)
                }
                
                matching_projects.append(project_info)
                sent_projects.add(coin_id)
                
                logger.info(f"Found matching project: {project_info['name']}")
            
            # Small delay to respect rate limits
            await asyncio.sleep(1.5)
        
        return matching_projects


def format_quick_summary(project):
    """Format a quick summary message"""
    categories = ', '.join(project['categories'][:3]) if project['categories'] else 'N/A'
    
    message = f"🚀 *New Project Alert!*\n\n"
    message += f"*{project['name']}* (${project['symbol']})\n\n"
    message += f"📊 *Stats:*\n"
    message += f"• X Followers: {project['twitter_followers']}\n"
    message += f"• Telegram: {project['telegram_users']} members\n"
    message += f"• Categories: {categories}\n\n"
    
    if project['market_cap']:
        message += f"💰 Market Cap: ${project['market_cap']:,.0f}\n\n"
    
    message += f"👀 Tap 'Full Details' for more info"
    
    return message


def format_full_details(project):
    """Format detailed project information"""
    message = f"📋 *Full Details: {project['name']}*\n\n"
    message += f"*Symbol:* ${project['symbol']}\n"
    message += f"*ID:* {project['id']}\n\n"
    
    message += f"*📊 Social Metrics:*\n"
    message += f"• X/Twitter: {project['twitter_followers']} followers\n"
    message += f"• Telegram: {project['telegram_users']} members\n\n"
    
    if project['categories']:
        message += f"*🏷️ Categories:*\n{', '.join(project['categories'])}\n\n"
    
    message += f"*🔗 Links:*\n"
    if project['homepage']:
        message += f"• Website: {project['homepage']}\n"
    if project['twitter']:
        message += f"• Twitter: https://twitter.com/{project['twitter']}\n"
    if project['telegram']:
        message += f"• Telegram: https://t.me/{project['telegram']}\n"
    if project['discord']:
        message += f"• Discord: {project['discord']}\n"
    
    if project['contract_address']:
        message += f"\n*📝 Contract:* `{project['contract_address']}`\n"
    
    if project['market_cap']:
        message += f"\n*💰 Market Cap:* ${project['market_cap']:,.0f}\n"
    
    # Truncate description
    desc = project['description'][:300] + "..." if len(project['description']) > 300 else project['description']
    if desc and desc != "No description available":
        message += f"\n*ℹ️ Description:*\n{desc}\n"
    
    return message


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    global USER_CHAT_ID
    USER_CHAT_ID = update.effective_chat.id
    
    welcome_message = (
        "🤖 *DeFi/NFT Project Scanner Bot*\n\n"
        "I'll automatically scan for new DeFi and NFT projects every hour!\n\n"
        "*Criteria:*\n"
        "• X/Twitter followers < 200\n"
        "• Telegram/Discord members < 50\n\n"
        "*Commands:*\n"
        "/start - Start the bot\n"
        "/scan - Manually trigger a scan\n"
        "/stats - View bot statistics\n\n"
        "Sit back and relax - I'll notify you when I find matching projects! 🚀"
    )
    
    await update.message.reply_text(welcome_message, parse_mode='Markdown')
    logger.info(f"Bot started for chat ID: {USER_CHAT_ID}")


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /scan command - manual scan"""
    await update.message.reply_text("🔍 Starting manual scan... This may take a minute.")
    
    scanner = ProjectScanner(COINGECKO_API_KEY)
    projects = await scanner.scan_for_projects()
    
    if projects:
        await update.message.reply_text(f"✅ Found {len(projects)} new project(s)!")
        for project in projects:
            await send_project_alert(context, project)
    else:
        await update.message.reply_text("No new projects found matching criteria.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    stats_message = (
        f"📊 *Bot Statistics*\n\n"
        f"Projects sent: {len(sent_projects)}\n"
        f"Status: Active ✅\n"
        f"Scan interval: Every hour\n"
    )
    await update.message.reply_text(stats_message, parse_mode='Markdown')


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    # Extract project ID from callback data
    project_id = query.data.replace('details_', '')
    
    # Fetch full details
    scanner = ProjectScanner(COINGECKO_API_KEY)
    details = await scanner.get_coin_details(project_id)
    
    if details:
        # Reconstruct project info
        community_data = details.get('community_data', {})
        links = details.get('links', {})
        
        project_info = {
            'id': project_id,
            'name': details.get('name', 'Unknown'),
            'symbol': details.get('symbol', '').upper(),
            'description': details.get('description', {}).get('en', 'No description available'),
            'twitter_followers': community_data.get('twitter_followers', 0) or 0,
            'telegram_users': community_data.get('telegram_channel_user_count', 0) or 0,
            'homepage': links.get('homepage', [''])[0] if links.get('homepage') else '',
            'twitter': links.get('twitter_screen_name', ''),
            'telegram': links.get('telegram_channel_identifier', ''),
            'discord': links.get('chat_url', [''])[0] if 'discord' in str(links.get('chat_url', '')).lower() else '',
            'categories': details.get('categories', []),
            'contract_address': details.get('contract_address', ''),
            'market_cap': details.get('market_data', {}).get('market_cap', {}).get('usd', 0)
        }
        
        full_details = format_full_details(project_info)
        await query.edit_message_text(full_details, parse_mode='Markdown')
    else:
        await query.edit_message_text("Sorry, couldn't fetch details for this project.")


async def send_project_alert(context: ContextTypes.DEFAULT_TYPE, project):
    """Send project alert to user"""
    if not USER_CHAT_ID:
        logger.warning("No user chat ID set. Use /start first.")
        return
    
    # Send quick summary with button
    summary = format_quick_summary(project)
    
    keyboard = [[InlineKeyboardButton("📄 Full Details", callback_data=f"details_{project['id']}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=USER_CHAT_ID,
        text=summary,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )


async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled hourly scan"""
    logger.info("Running scheduled scan...")
    
    scanner = ProjectScanner(COINGECKO_API_KEY)
    projects = await scanner.scan_for_projects()
    
    if projects and USER_CHAT_ID:
        for project in projects:
            await send_project_alert(context, project)
        logger.info(f"Sent {len(projects)} project alerts")
    else:
        logger.info("No new projects found in scheduled scan")


def main():
    """Start the bot"""
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Set up scheduler for hourly scans
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scheduled_scan,
        'interval',
        hours=1,
        args=[application],
        next_run_time=datetime.now()  # Run immediately on start
    )
    scheduler.start()
    
    # Start the bot
    logger.info("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
