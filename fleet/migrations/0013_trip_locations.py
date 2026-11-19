from django.db import migrations, models
import django.core.validators
import django.utils.timezone

def seed_state(apps,schema_editor):
    apps.get_model('fleet','MapSearchState').objects.using(schema_editor.connection.alias).get_or_create(pk=1)

class Migration(migrations.Migration):
    dependencies=[('fleet','0012_trip_cancellation_notice')]
    operations=[
        migrations.AddField(model_name='trip',name=stop+'_'+axis,field=models.DecimalField(blank=True,null=True,max_digits=9,decimal_places=6,verbose_name=label,validators=[django.core.validators.MinValueValidator(-bound),django.core.validators.MaxValueValidator(bound)]))
        for stop,axis,label,bound in [('origin','lat','Широта завантаження',90),('origin','lon','Довгота завантаження',180),('destination','lat','Широта розвантаження',90),('destination','lon','Довгота розвантаження',180)]
    ]+[
        migrations.CreateModel(name='MapSearchState',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('last_requested_at',models.DateTimeField(null=True))]),
        migrations.CreateModel(name='MapSearchCache',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('key',models.CharField(max_length=64,unique=True)),('results',models.JSONField(default=list)),('cached_at',models.DateTimeField(default=django.utils.timezone.now))]),
        migrations.RunPython(seed_state,migrations.RunPython.noop),
    ]
