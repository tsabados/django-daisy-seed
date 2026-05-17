from django.db import migrations, models
import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('social_media', '0015_remove_twitter_platform'),
    ]

    operations = [
        migrations.AlterField(
            model_name='socialmediapost',
            name='topic',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='socialmediapost',
            name='media_type',
            field=core.fields.TruncatingCharField(default='image', max_length=10),
        ),
        migrations.AddField(
            model_name='socialmediapost',
            name='video_type',
            field=core.fields.TruncatingCharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='socialmediapost',
            name='video_brief',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
