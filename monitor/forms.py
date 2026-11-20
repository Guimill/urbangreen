from datetime import date

from django import forms


def default_start(today=None):
    """December, two years back: covers at least two winters and one full year of seasons."""
    today = today or date.today()
    return date(today.year - 2, 12, 1)


class RunForm(forms.Form):
    start = forms.DateField(label="Du", initial=default_start, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    end = forms.DateField(label="Au", initial=date.today,
                          widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    max_cloud = forms.IntegerField(label="Nuages max. (%)", initial=40, min_value=5, max_value=100,
                                   help_text="Couverture nuageuse annoncée de la tuile Sentinel-2")
    per_month = forms.IntegerField(label="Dates par mois", initial=3, min_value=1, max_value=12,
                                   help_text="Les moins nuageuses sont gardées")
    refresh_osm = forms.BooleanField(label="Retélécharger les espaces verts OpenStreetMap", required=False)

    def clean(self):
        data = super().clean()
        start, end = data.get("start"), data.get("end")
        if start and end:
            if start > end:
                raise forms.ValidationError("La date de début doit précéder la date de fin.")
            if start < date(2017, 3, 28):
                raise forms.ValidationError("Les données Sentinel-2 L2A commencent en 2017.")
            if end > date.today():
                data["end"] = date.today()
        return data


LANDSAT_START = date(2013, 4, 11)  # Landsat 8 operational


def default_heat_start(today=None):
    """Ten years back: enough summers for a seasonal normal of every 200 m cell."""
    today = today or date.today()
    return date(today.year - 10, 12, 1)


class HeatRunForm(forms.Form):
    start = forms.DateField(label="Du", initial=default_heat_start,
                            widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                            help_text="Plusieurs années permettent de comparer une saison à sa normale")
    end = forms.DateField(label="Au", initial=date.today, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    max_cloud = forms.IntegerField(label="Nuages max. (%)", initial=50, min_value=5, max_value=100,
                                   help_text="Couverture nuageuse annoncée de la scène Landsat")

    def clean(self):
        data = super().clean()
        start, end = data.get("start"), data.get("end")
        if start and end:
            if start > end:
                raise forms.ValidationError("La date de début doit précéder la date de fin.")
            if start < LANDSAT_START:
                raise forms.ValidationError("Les données Landsat 8 commencent en avril 2013.")
            if end > date.today():
                data["end"] = date.today()
        return data
