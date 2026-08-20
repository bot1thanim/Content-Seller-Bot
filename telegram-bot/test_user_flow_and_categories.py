import asyncio
import importlib.util
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

BOT_PATH = Path('/home/ubuntu/Content-Seller-Bot/telegram-bot/bot.py')
spec = importlib.util.spec_from_file_location('content_seller_bot_updated', BOT_PATH)
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


class FakeMessage:
    def __init__(self, video=None, text=None):
        self.video = video
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeQuery:
    def __init__(self, data=''):
        self.data = data
        self.from_user = SimpleNamespace(id=bot.ADMIN_ID, first_name='Admin')
        self.edits = []
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def delete_message(self):
        self.deleted = True


class FakeBot:
    def __init__(self):
        self.sent_video_ids = []
        self.next_message_id = 1
        self.sent_messages = []
        self.deleted_ids = []

    async def send_video(self, chat_id, video, **kwargs):
        self.sent_video_ids.append((chat_id, video))
        result = SimpleNamespace(message_id=self.next_message_id)
        self.next_message_id += 1
        return result

    async def send_message(self, chat_id, text, **kwargs):
        self.sent_messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=self.next_message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted_ids.append((chat_id, message_id))
        return True


async def run_tests():
    original_paths = {
        'VIDEOS_FILE': bot.VIDEOS_FILE,
        'USERS_FILE': bot.USERS_FILE,
        'SETTINGS_FILE': bot.SETTINGS_FILE,
        'COINS_FILE': bot.COINS_FILE,
    }
    with tempfile.TemporaryDirectory() as tempdir:
        base = Path(tempdir)
        bot.VIDEOS_FILE = base / 'videos.json'
        bot.USERS_FILE = base / 'users.json'
        bot.SETTINGS_FILE = base / 'settings.json'
        bot.COINS_FILE = base / 'coins.json'
        bot.save_json(bot.SETTINGS_FILE, {'categories': ['כללי', 'ישראלי', 'חו״ל'], 'maintenance': False})
        bot.save_json(bot.USERS_FILE, {'100': {'seen_videos': ['seen-id']}})
        bot.save_json(bot.VIDEOS_FILE, [
            {'entry_id': '1', 'file_id': 'seen-id', 'duration': 10, 'category': 'כללי'},
            {'entry_id': '2', 'file_id': 'fresh-a', 'duration': 15, 'category': 'ישראלי'},
            {'entry_id': '3', 'file_id': 'fresh-b', 'duration': 20, 'category': 'כללי'},
            {'entry_id': '4', 'file_id': 'broken-id', 'duration': 25, 'category': 'כללי', 'file_status': 'broken'},
            {'entry_id': '5', 'file_id': 'israeli-b', 'duration': 12, 'category': 'ישראלי'},
        ])
        try:
            # The referral and purchase-help buttons are intentionally swapped in the main user keyboard.
            main_keyboard = bot.get_main_keyboard(100)
            main_callbacks = [
                button.callback_data for row in main_keyboard.inline_keyboard for button in row
            ]
            assert main_callbacks.index('referrals') < main_callbacks.index('purchase_help')

            # Public delivery remains random, never repeats prior deliveries, and excludes broken records.
            context = SimpleNamespace(bot=FakeBot(), user_data={})
            assert bot.count_unseen_videos(100) == 3, 'Availability must ignore seen and broken videos'
            sent = await bot.send_videos_to_user(context, 100, 3)
            assert sent == 3, 'All three unseen valid videos should be delivered'
            delivered = {file_id for _, file_id in context.bot.sent_video_ids}
            assert delivered == {'fresh-a', 'fresh-b', 'israeli-b'}, f'Unexpected delivery: {delivered}'
            assert await bot.send_videos_to_user(context, 100, 1) == 0, 'Previously delivered videos must never repeat'

            # A video upload is stored immediately in the default category without category or preview questions.
            upload_video = SimpleNamespace(
                file_id='upload-id', file_unique_id='upload-unique', file_name='batch_01.mp4',
                duration=31, file_size=456789,
            )
            upload_message = FakeMessage(video=upload_video)
            upload_update = SimpleNamespace(effective_user=SimpleNamespace(id=bot.ADMIN_ID), message=upload_message)
            state = await bot.handle_video(upload_update, SimpleNamespace(user_data={}))
            assert state == bot.ConversationHandler.END, 'Upload must end immediately, without category conversation'
            videos = bot.load_json(bot.VIDEOS_FILE)
            added = next(item for item in videos if item['file_id'] == 'upload-id')
            assert added['category'] == 'רנדומלי' and added['preview'] is None, 'Upload must use the random default category and no preview'
            assert 'קטגוריה: רנדומלי' in upload_message.replies[-1][0], 'Upload confirmation is missing the random default category'

            # One hundred sequential updates must all be stored immediately; no upload is skipped for category/preview input.
            for index in range(2, 101):
                batch_video = SimpleNamespace(
                    file_id=f'batch-id-{index}', file_unique_id=f'batch-unique-{index}',
                    file_name=f'batch_{index:02}.mp4', duration=20 + index, file_size=100000 + index,
                )
                batch_message = FakeMessage(video=batch_video)
                batch_update = SimpleNamespace(effective_user=SimpleNamespace(id=bot.ADMIN_ID), message=batch_message)
                assert await bot.handle_video(batch_update, SimpleNamespace(user_data={})) == bot.ConversationHandler.END
            stored_batch_ids = {
                item['file_id'] for item in bot.load_json(bot.VIDEOS_FILE)
                if str(item.get('file_id', '')).startswith('batch-id-')
            }
            assert stored_batch_ids == {f'batch-id-{index}' for index in range(2, 101)}, 'One or more batch videos were skipped'

            # User-facing purchase help explains random delivery and exposes no categories.
            help_query = FakeQuery('purchase_help')
            await bot.purchase_help(
                SimpleNamespace(callback_query=help_query, effective_user=SimpleNamespace(id=12345)),
                SimpleNamespace(),
            )
            help_text = help_query.edits[-1][0]
            assert 'באקראי' in help_text and 'לא בוחרים סרטון ספציפי' in help_text, 'Purchase help must explain random delivery'
            assert 'קטגור' not in help_text, 'Purchase help must not expose categories'

            # Gallery root exposes private categories but top-level search has moved into browsing.
            gallery_query = FakeQuery('admin_gallery')
            await bot.admin_gallery(
                SimpleNamespace(callback_query=gallery_query, effective_user=SimpleNamespace(id=bot.ADMIN_ID)),
                SimpleNamespace(),
            )
            gallery_markup = gallery_query.edits[-1][1]['reply_markup']
            root_callbacks = [button.callback_data for row in gallery_markup.inline_keyboard for button in row]
            assert 'admin_categories' in root_callbacks, 'Private categories menu is missing from gallery'
            assert 'admin_search_sec_start' not in root_callbacks, 'Time search must not be at gallery root'

            # Number search returns the requested library item and offers a return to browsing.
            number_context = SimpleNamespace(bot=FakeBot(), user_data={})
            number_message = FakeMessage(text='2')
            number_update = SimpleNamespace(effective_user=SimpleNamespace(id=bot.ADMIN_ID), message=number_message)
            state = await bot.admin_video_search_input(number_update, number_context)
            assert state == bot.ConversationHandler.END
            assert number_context.bot.sent_video_ids[-1][1] == 'fresh-a', 'Number search did not show requested item'
            result_markup = number_message.replies[-1][1]['reply_markup']
            result_callbacks = [button.callback_data for row in result_markup.inline_keyboard for button in row]
            assert 'vid_page_1' in result_callbacks, 'Number-search result must return to the corresponding browse page'
            assert 'admin_search_sec_start' in result_callbacks, 'Number-search result must offer smart time search'

            # Category browsing shows category count, permits single-item browsing, and sends all category videos only to admin.
            category_context = SimpleNamespace(bot=FakeBot(), user_data={})
            category_menu_query = FakeQuery('admin_cat_browse')
            await bot.admin_cat_browse_menu(SimpleNamespace(callback_query=category_menu_query), category_context)
            category_menu_callbacks = [
                button.callback_data
                for row in category_menu_query.edits[-1][1]['reply_markup'].inline_keyboard
                for button in row
            ]
            assert 'cat_browse_pick_1' in category_menu_callbacks, 'ישראלי category is missing from browse menu'

            category_pick_query = FakeQuery('cat_browse_pick_1')
            await bot.admin_cat_browse_category(SimpleNamespace(callback_query=category_pick_query), category_context)
            assert category_context.user_data['category_browse_name'] == 'ישראלי'
            assert '2' in category_pick_query.edits[-1][0], 'Category count is incorrect'

            category_page_query = FakeQuery('cat_browse_page_0')
            await bot.admin_cat_browse_page(SimpleNamespace(callback_query=category_page_query), category_context)
            assert category_context.bot.sent_video_ids[-1][1] == 'israeli-b', 'Category browse must sort videos by duration'

            category_send_query = FakeQuery('cat_browse_send_all')
            await bot.admin_cat_browse_send_all(SimpleNamespace(callback_query=category_send_query), category_context)
            sent_category_ids = [file_id for _, file_id in category_context.bot.sent_video_ids]
            assert sent_category_ids[-2:] == ['israeli-b', 'fresh-a'], 'Send-all category flow must send exactly all matching videos in duration order'
            assert category_context.bot.sent_messages and 'ישראלי' in category_context.bot.sent_messages[-1][1], 'Category send-all summary is missing'

            # Category sorting starts with the shortest video, marks its category, and deletes it before moving on.
            sort_query = FakeQuery('cat_sort_page_0')
            sort_context = SimpleNamespace(bot=FakeBot(), user_data={})
            await bot.admin_cat_sort_page(SimpleNamespace(callback_query=sort_query), sort_context, 0)
            sort_markup = sort_query.edits[-1][1]['reply_markup']
            sort_labels = [button.text for row in sort_markup.inline_keyboard for button in row]
            assert any(label.startswith('✅ ') for label in sort_labels), 'Current category must be visibly marked'
            assert '10 שניות' in sort_query.edits[-1][0], 'Category sorting must start from the shortest video'
            assert len(sort_context.bot.sent_video_ids) == 1, 'Sorting must send exactly one current video'
            next_sort_query = FakeQuery('cat_sort_page_1')
            await bot.admin_cat_sort_navigation(SimpleNamespace(callback_query=next_sort_query), sort_context)
            assert sort_context.bot.deleted_ids, 'Moving to the next category-sort video must delete the prior preview'
            assert len(sort_context.bot.sent_video_ids) == 2, 'Moving next must send exactly one replacement preview'
            assert '12 שניות' in next_sort_query.edits[-1][0], 'Category sorting must remain in ascending duration order'

            # Categories can be moved into a manual order and restored to Hebrew alphabetical order.
            order_context = SimpleNamespace(user_data={})
            order_move_query = FakeQuery('cat_order_down_0')
            await bot.admin_cat_order_move(SimpleNamespace(callback_query=order_move_query), order_context)
            manual_settings = bot.load_settings()
            assert manual_settings['category_order_mode'] == 'manual', 'Moving a category must switch to manual order'
            assert manual_settings['categories'][0] != 'חו״ל', 'The selected category was not moved down'
            order_alpha_query = FakeQuery('cat_order_alpha')
            await bot.admin_cat_order_alphabetical(SimpleNamespace(callback_query=order_alpha_query), order_context)
            alphabetical_settings = bot.load_settings()
            assert alphabetical_settings['category_order_mode'] == 'alphabetical'
            assert alphabetical_settings['categories'] == sorted(alphabetical_settings['categories'], key=lambda name: name.casefold())

            # Renaming a category updates both settings and assigned videos.
            rename_message = FakeMessage(text='מקומי')
            rename_update = SimpleNamespace(message=rename_message)
            rename_context = SimpleNamespace(user_data={'category_rename_old': 'ישראלי'})
            state = await bot.admin_cat_rename_input(rename_update, rename_context)
            assert state == bot.ConversationHandler.END
            assert 'מקומי' in bot.load_settings()['categories'], 'Renamed category missing from settings'
            assert any(item.get('category') == 'מקומי' for item in bot.load_json(bot.VIDEOS_FILE)), 'Assigned videos were not renamed'
        finally:
            for name, value in original_paths.items():
                setattr(bot, name, value)

    source = BOT_PATH.read_text(encoding='utf-8')
    required_markers = [
        'callback_data="purchase_help"',
        'InlineKeyboardButton("🔢 חיפוש לפי מספר", callback_data="admin_video_search")',
        'InlineKeyboardButton("⏱ חיפוש לפי זמן", callback_data="admin_search_sec_start")',
        'InlineKeyboardButton("🏷 קטגוריות", callback_data="admin_categories")',
        'CallbackQueryHandler(admin_cat_rename_pick, pattern=r"^cat_rename_pick_\\d+$")',
        'states={},',
        'callback_data="admin_cat_browse"',
        'callback_data="cat_browse_send_all"',
        'async def admin_cat_browse_send_all',
    ]
    for marker in required_markers:
        assert marker in source, f'Missing code marker: {marker}'


asyncio.run(run_tests())
print('PASS: random no-repeat delivery, default bulk upload, purchase help, private categories, sorting, and rename flow.')
