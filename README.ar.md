# Mahran MCP — الدليل بالعربي

حزمة لتوصيل Codex أو Claude بـGemini وموديلات Ollama المحلية. تقدر كمان تضيف الطرفية والوصول لمجلدات تختارها. كل مستخدم بيستخدم مفتاحه وموديلاته؛ الحزمة مش بتشارك حساب صاحب المشروع.

## التثبيت

محتاج Python 3.11 أو أحدث. نزّل المشروع وافتح الطرفية داخل مجلده:

```sh
git clone https://github.com/Khalid-Mahran/mahran-mcp.git
cd mahran-mcp
python3 -m venv .venv
.venv/bin/python -m pip install --prefer-binary .
.venv/bin/python -m mahran_mcp.configure --save-gemini-key
```

الأمر الأخير بيطلب مفتاح Gemini من غير ما يعرضه، وبيحفظه خارج المشروع. ما ترفعش المفتاح على GitHub. على Windows استبدل `.venv/bin/python` بـ`.venv\Scripts\python.exe`. الاختبارات المحلية تمت على macOS؛ Windows لسه محتاج تجربة فعلية.

لتشغيل الموديل المحلي، ثبّت Ollama وشغّله، ثم:

```sh
ollama pull qwen2.5:7b
.venv/bin/python -m mahran_mcp.configure --client both
```

هتلاقي إعدادات Codex وClaude في مجلد `generated`. ادمجها في إعدادات برنامجك من غير مسح الإعدادات الموجودة، ثم اقفله وافتحه تاني.

لإضافة الملفات والطرفية، محتاج Node.js 24 أو أحدث وnpm:

```sh
.venv/bin/python -m mahran_mcp.configure --client both --with-terminal --install-filesystem --filesystem-dir "/absolute/path/to/your/project"
```

بدّل المسار بمجلد موجود عندك. الطرفية تقدر تنفّذ أوامر بصلاحيات حسابك؛ فعّلها بس لو محتاجها. محتوى Gemini بيتبعت لـGoogle، ومحتوى Ollama بيروح للعنوان اللي ضبطته، وهو جهازك افتراضيًا.

ارفع ملفات المشروع على GitHub، لكن ما ترفعش `generated` أو `.venv` أو المفاتيح أو ملفاتك الشخصية. الحزمة فيها `.gitignore` لاستبعاد الملفات دي.

[التعليمات الكاملة وخطوات حل المشاكل بالإنجليزي](README.md)
