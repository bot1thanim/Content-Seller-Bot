async def admin_coupon_new_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("🎟 *קופון חדש*\n\nשלח את *קוד הקופון* (אותיות/מספרים):", parse_mode="Markdown")
    return ADMIN_COUPON_CODE

async def admin_coupon_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    code = update.message.text.strip().upper()
    if not code.replace("_", "").replace("-", "").isalnum():
        await update.message.reply_text("❌ קוד לא תקין. רק אותיות ומספרים.")
        return ADMIN_COUPON_CODE
    coupons = load_json(COUPONS_FILE)
    if code in coupons:
        await update.message.reply_text("❌ קוד כבר קיים.")
        return ADMIN_COUPON_CODE
    context.user_data["new_coupon_code"] = code
    await update.message.reply_text(f"✅ קוד: `{code}`\n\nכמה 🪙 מטבעות?", parse_mode="Markdown")
    return ADMIN_COUPON_COINS

async def admin_coupon_get_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        val = int(update.message.text.strip())
        if val <= 0:
            raise ValueError
        context.user_data["new_coupon_coins"] = val
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין.")
        return ADMIN_COUPON_COINS
    await update.message.reply_text("📅 תאריך תפוגה? (`YYYY-MM-DD` או `skip`):", parse_mode="Markdown")
    return ADMIN_COUPON_EXPIRY

