# -*- coding: utf-8 -*-
# Generated by Django 1.9.13 on 2017-09-08 13:27
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mcm', '0012_document_doc_type'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='notice',
            name='references',
        ),
        migrations.AddField(
            model_name='document',
            name='references',
            field=models.ManyToManyField(to='mcm.Reference', verbose_name='reference'),
        ),
    ]