from django import forms

from .models import Event


class EventFilters(forms.Form):
    date = forms.DateField(label="Date", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    category = forms.ChoiceField(required=False, choices=[("", "All interests"), *Event.Category.choices])
    neighborhood = forms.CharField(required=False, max_length=120,
                                   widget=forms.TextInput(attrs={"list": "neighborhoods", "placeholder": "Any neighborhood"}))
    min_price = forms.DecimalField(label="Min price (USD)", required=False, min_value=0, max_digits=10, decimal_places=2)
    max_price = forms.DecimalField(label="Max price (USD)", required=False, min_value=0, max_digits=10, decimal_places=2)
    free = forms.BooleanField(label="Free only", required=False, widget=forms.CheckboxInput(attrs={"value": "1"}))

    def clean_date(self):
        value = self.cleaned_data["date"]
        if value and not 1900 <= value.year <= 9998:
            raise forms.ValidationError("Choose a year between 1900 and 9998.")
        return value

    def clean(self):
        data = super().clean()
        minimum, maximum = data.get("min_price"), data.get("max_price")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise forms.ValidationError("Minimum price must not exceed maximum price.")
        return data
